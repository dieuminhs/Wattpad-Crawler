import logging
import threading
import time

import httpx

from wattpad_crawler.config import Config

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
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            self._bucket.take()
            try:
                resp = self._client.get(url, **kwargs)
            except httpx.RequestError as e:
                last_exc = e
                self._sleep_backoff(attempt)
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", "60"))
                logger.warning("429 on %s — sleeping %.1fs", url, wait)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                self._sleep_backoff(attempt)
                continue
            resp.raise_for_status()
            return resp
        if last_exc:
            raise last_exc
        resp.raise_for_status()
        return resp

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(2 ** (attempt - 1), 16))

    def close(self) -> None:
        self._client.close()
