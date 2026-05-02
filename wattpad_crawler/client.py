import threading
import time

import httpx

from wattpad_crawler.config import Config


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
