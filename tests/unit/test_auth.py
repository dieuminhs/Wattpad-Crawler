import httpx
import pytest

from local_story_archive.auth import AuthError, validate_cookie
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import Config


def make_client(tmp_path, transport, cookie="valid-cookie-value"):
    cfg = Config(output_dir=tmp_path, cookie=cookie, rate_limit_per_sec=1000.0)
    rlc = RateLimitedClient(cfg)
    # Replace the underlying httpx client with one wired to MockTransport.
    # Preserve the cookie jar so the blank-cookie short-circuit works.
    jar = rlc._client.cookies
    rlc._client = httpx.Client(
        transport=transport,
        headers={"User-Agent": cfg.user_agent},
        cookies=jar,
    )
    return rlc


def test_validate_cookie_raises_on_401(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(401))
    rlc = make_client(tmp_path, transport)
    try:
        with pytest.raises(AuthError):
            validate_cookie(rlc)
    finally:
        rlc.close()


def test_validate_cookie_raises_on_403(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(403))
    rlc = make_client(tmp_path, transport)
    try:
        with pytest.raises(AuthError):
            validate_cookie(rlc)
    finally:
        rlc.close()


def test_validate_cookie_raises_on_login_redirect(tmp_path):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(302, headers={"Location": "https://www.wattpad.com/login?next=/"})
    )
    rlc = make_client(tmp_path, transport)
    try:
        with pytest.raises(AuthError) as exc_info:
            validate_cookie(rlc)
        msg = str(exc_info.value).lower()
        assert "login" in msg or "redirect" in msg
    finally:
        rlc.close()


def test_validate_cookie_passes_on_200(tmp_path):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"stories": []})
    )
    rlc = make_client(tmp_path, transport)
    try:
        # Returns None on success (does not raise).
        result = validate_cookie(rlc)
        assert result is None
    finally:
        rlc.close()


def test_validate_cookie_propagates_network_error(tmp_path):
    def handler(req):
        raise httpx.ConnectError("simulated DNS failure")
    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    try:
        # Network errors propagate as httpx.RequestError (or subclass), NOT AuthError.
        with pytest.raises(httpx.RequestError):
            validate_cookie(rlc)
    finally:
        rlc.close()


def test_validate_cookie_short_circuits_on_blank(tmp_path):
    """D-04 / RESEARCH note: blank cookie raises AuthError WITHOUT making an HTTP call."""
    cfg = Config(output_dir=tmp_path, cookie="", rate_limit_per_sec=1000.0)
    rlc = RateLimitedClient(cfg)
    try:
        # If validate_cookie reaches the HTTP call, this monkeypatch fails the test.
        # Patch the PUBLIC RateLimitedClient.get (what validate_cookie actually calls)
        # rather than the private rlc._client.get -- catches both "short-circuit
        # removed" and "short-circuit present but ineffective" failure modes.
        original_get = rlc.get
        def fail_if_called(*args, **kwargs):
            pytest.fail(
                "validate_cookie made an HTTP call despite empty cookie — "
                "short-circuit broken"
            )
        rlc.get = fail_if_called
        with pytest.raises(AuthError):
            validate_cookie(rlc)
    finally:
        rlc.get = original_get
        rlc.close()


def test_validate_cookie_raises_on_400_permission_denied(tmp_path):
    """Wattpad's actual unauth response — HTTP 400 + error_type:'PermissionDenied'.

    Verified manually 2026-05-03 against live Wattpad API: both no-cookie and
    bogus-cookie requests to users/wattpad/library?limit=1 return this shape,
    NOT 401/403/redirect. See 02-01-PLAN.md Task 1 resolution.
    """
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            400,
            json={
                "error_code": 1018,
                "error_type": "PermissionDenied",
                "message": "User not logged in",
            },
        )
    )
    rlc = make_client(tmp_path, transport)
    try:
        with pytest.raises(AuthError) as exc_info:
            validate_cookie(rlc)
        msg = str(exc_info.value)
        assert "PermissionDenied" in msg or "1018" in msg
    finally:
        rlc.close()
