"""Cookie validation and auth-failure exception types.

Probes Wattpad's current-user endpoint to verify the configured session cookie
is accepted.
"""
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from local_story_archive.client import RateLimitedClient

logger = logging.getLogger(__name__)

# Probe URL used by the web UI before saving cookies and before auth-required
# jobs. Use the logged-in-user endpoint instead of another user's library:
# valid tokens can be rejected by user-scoped library privacy rules even though
# they work for the owner's own library.
_PROBE_URL = "https://www.wattpad.com/api/v3/users/me"


class AuthError(Exception):
    """Cookie validation failed at startup or /setup POST."""
    pass


class AuthFailedError(AuthError):
    """Wattpad returned 401/403 mid-job. Subclass of AuthError so callers
    can `except AuthError` to catch both startup and mid-job variants."""
    def __init__(self, message: str, *, status_code: int, url: str):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


def validate_cookie(client: "RateLimitedClient") -> None:
    """Probe Wattpad to verify the configured cookie is accepted.

    Raises:
        AuthError: cookie missing/empty, or Wattpad returned 401/403/redirect-to-login.
        httpx.RequestError: network/transport failure (caller decides how to handle).

    Returns None on a 2xx response.
    """
    # D-04 / RESEARCH §"Test Infrastructure Survey" Note on blank cookie:
    # short-circuit on empty cookie — no point hitting the network. The
    # client carries the cookie via its httpx.Cookies jar; reach in to
    # check rather than re-load Config.
    cookie_jar = getattr(client._client, "cookies", None)
    token = ""
    if cookie_jar is not None:
        token = cookie_jar.get("token", domain="wattpad.com") or ""
    if not token.strip():
        raise AuthError(
            "no cookie configured — set one via /setup or edit _config.toml"
        )

    # follow_redirects=False so a 3xx-to-/login is observable instead of
    # silently followed to a 200 login HTML page (httpx default for the
    # main client is follow_redirects=True per build_client).
    # max_attempts=1 because auth failures are deterministic (D-04, D-14).
    try:
        resp = client.get(_PROBE_URL, max_attempts=1, follow_redirects=False)
    except AuthFailedError as e:
        # 401/403 came back via the client.py D-13 branch — re-raise as
        # AuthError so /setup POST and CLI handlers can catch the unified
        # base class. (AuthFailedError IS an AuthError, so this also works
        # for `except AuthError` callers; the wrap is for message clarity.)
        raise AuthError(str(e)) from e
    except httpx.HTTPStatusError as e:
        # client.get() calls raise_for_status() which raises HTTPStatusError
        # for any non-2xx response (3xx, 4xx, 5xx). Convert to AuthError so
        # callers only need to catch the canonical exception hierarchy.
        status = e.response.status_code
        if 300 <= status < 400:
            location = e.response.headers.get("Location", "")
            if "/login" in location.lower():
                raise AuthError(
                    f"Wattpad redirected probe to login (HTTP {status}, "
                    f"Location={location!r}) — cookie was rejected"
                ) from e
            logger.warning(
                "Probe redirected to %r (status %d) — not /login, treating as success",
                location, status,
            )
            return
        # Wattpad's actual unauth response is HTTP 400 + structured JSON body
        # (verified manually 2026-05-03 against live API — see 02-01-PLAN Task 1
        # resolution). NOT 401/403/redirect as the API would suggest.
        if status == 400:
            try:
                body = e.response.json()
            except ValueError:
                body = {}
            if (
                body.get("error_type") == "PermissionDenied"
                or body.get("error_code") == 1018
            ):
                raise AuthError(
                    f"Wattpad rejected probe (HTTP 400, "
                    f"error_type={body.get('error_type')!r}, "
                    f"error_code={body.get('error_code')!r}) — "
                    "cookie was rejected or incomplete"
                ) from e
        raise AuthError(
            f"Wattpad rejected probe (HTTP {status}) — cookie was rejected"
        ) from e

    if 200 <= resp.status_code < 300:
        return
    raise AuthError(f"Probe returned unexpected HTTP {resp.status_code}")
