from pathlib import Path

from wattpad_crawler.client import build_client
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
