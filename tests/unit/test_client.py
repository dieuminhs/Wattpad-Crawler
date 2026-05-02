import threading
import time
from pathlib import Path

from wattpad_crawler.client import TokenBucket, build_client
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
