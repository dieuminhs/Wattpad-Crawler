import threading
import time
from pathlib import Path

import httpx
import pytest

from wattpad_crawler.client import RateLimitedClient, TokenBucket, build_client
from wattpad_crawler.config import Config


def test_client_sets_user_agent(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="abc", user_agent="ua/1")
    client = build_client(cfg)
    try:
        assert client.headers["User-Agent"] == "ua/1"
    finally:
        client.close()


def test_client_attaches_cookie(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="my-token")
    client = build_client(cfg)
    try:
        # Cookie is scoped to wattpad.com — query with that domain
        assert client.cookies.get("token", domain="wattpad.com") == "my-token"
        # Sanity: no cookie sent to unrelated domains
        assert client.cookies.get("token", domain="evil.example.com") is None
    finally:
        client.close()


def test_client_no_cookie_when_empty(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="")
    client = build_client(cfg)
    try:
        assert client.cookies.get("token", domain="wattpad.com") is None
    finally:
        client.close()


def test_token_bucket_blocks_when_empty():
    bucket = TokenBucket(rate_per_sec=10.0, capacity=2)
    bucket.take()
    bucket.take()
    start = time.monotonic()
    bucket.take()  # should sleep ~0.1s
    elapsed = time.monotonic() - start
    assert 0.05 < elapsed < 0.3


def test_token_bucket_does_not_block_when_full():
    bucket = TokenBucket(rate_per_sec=1.0, capacity=3)
    start = time.monotonic()
    for _ in range(3):
        bucket.take()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


def test_token_bucket_under_concurrency_does_not_deadlock():
    """Multi-thread test: catches the spinlock bug.

    With rate=20/s and capacity=2, two threads each calling take() 4 times
    needs ~6 tokens beyond the initial capacity → ~0.3s. If the lock is
    held during sleep (the bug), this test deadlocks the thread that's
    waiting on the lock and the test hits its timeout.
    """
    bucket = TokenBucket(rate_per_sec=20.0, capacity=2)
    errors: list[str] = []

    def worker():
        try:
            for _ in range(4):
                bucket.take()
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
        assert not t.is_alive(), "Thread deadlocked — TokenBucket holds lock during sleep"
    elapsed = time.monotonic() - start
    assert not errors, f"Worker threw: {errors}"
    # 8 takes total, 2 free from capacity, 6 need to be refilled at 20/s = 0.3s.
    # Allow generous upper bound for slow CI; lower bound asserts the bucket
    # actually rate-limited (not just instant).
    assert 0.2 < elapsed < 1.5, f"Elapsed {elapsed} outside expected window"


def make_client(tmp_path, transport):
    cfg = Config(output_dir=tmp_path, rate_limit_per_sec=1000.0)
    rlc = RateLimitedClient(cfg)
    rlc._client = httpx.Client(transport=transport, headers={"User-Agent": cfg.user_agent})
    return rlc


def test_client_retries_on_5xx(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    r = rlc.get("https://example.com/x")
    assert r.status_code == 200
    assert calls["n"] == 3
    rlc.close()


def test_client_gives_up_after_max_attempts(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    rlc = make_client(tmp_path, transport)
    with pytest.raises(httpx.HTTPStatusError):
        rlc.get("https://example.com/x", max_attempts=3)
    rlc.close()


def test_client_honors_retry_after_on_429(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    start = time.monotonic()
    r = rlc.get("https://example.com/x")
    assert r.status_code == 200
    assert (time.monotonic() - start) >= 0.9
    rlc.close()


def test_client_clears_last_exc_after_response(tmp_path):
    """Network errors followed by a 5xx must surface the 5xx, not the network error."""
    states = ["raise", "raise", "503"]

    def handler(request: httpx.Request) -> httpx.Response:
        action = states.pop(0)
        if action == "raise":
            raise httpx.ConnectError("boom")
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    with pytest.raises(httpx.HTTPStatusError):
        rlc.get("https://example.com/x", max_attempts=3)
    rlc.close()


def test_client_handles_unparseable_retry_after(tmp_path):
    """Non-numeric Retry-After (e.g. HTTP-date) falls back gracefully."""
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "Sat, 02 May 2026 18:00:00 GMT"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    # Use small max_attempts so we don't actually wait 60s; we'll patch the sleep
    import unittest.mock
    with unittest.mock.patch("wattpad_crawler.client.time.sleep") as sleep_mock:
        r = rlc.get("https://example.com/x")
    assert r.status_code == 200
    # Confirm the sleep was called with the fallback 60s, not crashed
    assert any(call.args and call.args[0] == 60.0 for call in sleep_mock.call_args_list)
    rlc.close()


def test_client_caps_retry_after_at_300s(tmp_path):
    """A malicious server sending a giant Retry-After can't stall us forever."""
    rlc = RateLimitedClient(Config(output_dir=tmp_path, rate_limit_per_sec=1000.0))
    try:
        assert rlc._parse_retry_after("86400") == 300.0
        assert rlc._parse_retry_after("garbage") == 60.0
        assert rlc._parse_retry_after(None) == 60.0
        assert rlc._parse_retry_after("5") == 5.0
    finally:
        rlc.close()


def test_client_rejects_zero_max_attempts(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    rlc = make_client(tmp_path, transport)
    with pytest.raises(ValueError):
        rlc.get("https://example.com/x", max_attempts=0)
    rlc.close()


def test_client_supports_context_manager(tmp_path):
    cfg = Config(output_dir=tmp_path)
    with RateLimitedClient(cfg) as rlc:
        assert rlc._client is not None
    # After exit, close() has run; httpx.Client.is_closed should be True
    assert rlc._client.is_closed


def test_client_does_not_sleep_on_final_attempt(tmp_path):
    """No backoff after the final failed attempt — saves up to 16s."""
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    rlc = make_client(tmp_path, transport)
    import unittest.mock
    with unittest.mock.patch.object(rlc, "_sleep_backoff") as backoff_mock:
        with pytest.raises(httpx.HTTPStatusError):
            rlc.get("https://example.com/x", max_attempts=3)
    # 3 attempts, but backoff only between attempts → called at most 2 times
    assert backoff_mock.call_count == 2
    rlc.close()


def test_build_client_config_propagates_through_rate_limited_client(tmp_path):
    """Catches regressions in build_client (cookie jar, UA, timeouts) being silently dropped."""
    cfg = Config(output_dir=tmp_path, cookie="tok123", user_agent="ua/test")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200)

    rlc = RateLimitedClient(cfg)
    # Swap transport but PRESERVE headers/cookies from build_client.
    rlc._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers=dict(rlc._client.headers),
        cookies=rlc._client.cookies,
        follow_redirects=True,
    )
    rlc.get("https://www.wattpad.com/test")
    assert "ua/test" in seen["ua"]
    assert "tok123" in seen["cookie"]
    rlc.close()
