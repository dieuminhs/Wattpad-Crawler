import httpx

from wattpad_crawler.config import Config


def build_client(cfg: Config) -> httpx.Client:
    cookies: dict[str, str] = {}
    if cfg.cookie:
        cookies["token"] = cfg.cookie
    return httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        cookies=cookies,
        timeout=30.0,
        follow_redirects=True,
    )
