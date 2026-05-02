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
