import logging
import threading
import time

import httpx

from local_story_archive.config import Config

logger = logging.getLogger(__name__)


def build_client(cfg: Config) -> httpx.Client:
    jar = httpx.Cookies()
    if cfg.cookie:
        jar.set("token", cfg.cookie, domain="wattpad.com")
    return httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        cookies=jar,
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0),
        follow_redirects=True,
    )


class TokenBucket:
    """Simple thread-safe token bucket. Blocks on take() when empty."""

    def __init__(self, rate_per_sec: float, capacity: int = 5):
        self.rate = rate_per_sec
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                sleep_for = deficit / self.rate
            time.sleep(sleep_for)


class RateLimitedClient:
    def __init__(self, cfg: Config):
        self._client = build_client(cfg)
        cap = max(2, int(cfg.rate_limit_per_sec * 2))
        self._bucket = TokenBucket(cfg.rate_limit_per_sec, capacity=cap)

    def get(self, url: str, *, max_attempts: int = 5, **kwargs) -> httpx.Response:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        last_exc: Exception | None = None
        resp: httpx.Response | None = None
        for attempt in range(1, max_attempts + 1):
            self._bucket.take()
            logger.info("GET %s attempt %d", url, attempt)
            try:
                resp = self._client.get(url, **kwargs)
            except httpx.RequestError as e:
                logger.warning("GET %s attempt %d failed: %s", url, attempt, e)
                last_exc = e
                resp = None
                if attempt < max_attempts:
                    self._sleep_backoff(attempt)
                continue
            logger.info("HTTP %d %s attempt %d", resp.status_code, url, attempt)
            # We got a response — clear any stashed network error.
            last_exc = None

            # AUTH-04 / D-13: 401/403/400-PermissionDenied detection BEFORE
            # 429/5xx retry branches and BEFORE raise_for_status (which would
            # raise HTTPStatusError without our status_code/url payload).
            # D-14: do NOT retry — auth failures are deterministic.
            # Deferred import breaks the auth.py <-> client.py cycle (auth.py
            # imports RateLimitedClient via TYPE_CHECKING for typing only).
            if resp.status_code in (401, 403):
                from local_story_archive.auth import AuthFailedError

                logger.warning(
                    "Auth failure on %s — HTTP %d", url, resp.status_code,
                )
                raise AuthFailedError(
                    f"Wattpad returned HTTP {resp.status_code} for {url} — "
                    "cookie likely expired",
                    status_code=resp.status_code,
                    url=url,
                )

            # Wattpad's actual mid-job unauth signal is HTTP 400 with a
            # structured JSON body — verified manually 2026-05-03 against
            # live API (Plan 02-01 Task 1). NOT 401/403 as the API would
            # suggest. Only intercept the PermissionDenied / 1018 shape;
            # other HTTP 400s (e.g., InvalidEndpoint=1001) are real client
            # errors and must fall through to raise_for_status.
            if resp.status_code == 400:
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                if (
                    body.get("error_type") == "PermissionDenied"
                    or body.get("error_code") == 1018
                ):
                    from local_story_archive.auth import AuthFailedError

                    logger.warning(
                        "Auth failure on %s — HTTP 400 PermissionDenied", url,
                    )
                    raise AuthFailedError(
                        f"Wattpad returned HTTP 400 PermissionDenied for "
                        f"{url} — cookie likely expired",
                        status_code=400,
                        url=url,
                    )

            if resp.status_code == 429:
                wait = self._parse_retry_after(resp.headers.get("Retry-After"))
                logger.warning("429 on %s — sleeping %.1fs", url, wait)
                if attempt < max_attempts:
                    time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                if attempt < max_attempts:
                    self._sleep_backoff(attempt)
                continue
            resp.raise_for_status()
            return resp
        if last_exc:
            raise last_exc
        assert resp is not None  # narrowed: loop ran at least once and resp was set
        resp.raise_for_status()
        return resp

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(2 ** (attempt - 1), 16))

    @staticmethod
    def _parse_retry_after(raw: str | None) -> float:
        """Parse a Retry-After header. Accepts integer seconds; falls back to 60s
        for unparseable or missing values. Caps at 300s to bound worst case."""
        if raw is None:
            return 60.0
        try:
            wait = float(raw)
        except ValueError:
            logger.warning("Unparseable Retry-After header %r, defaulting to 60s", raw)
            wait = 60.0
        return min(wait, 300.0)

    def __enter__(self) -> "RateLimitedClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
