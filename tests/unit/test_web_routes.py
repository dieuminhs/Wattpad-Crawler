from pathlib import Path

from fastapi.testclient import TestClient

from wattpad_crawler.config import Config
from wattpad_crawler.web.app import build_app


def test_app_health_endpoint(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/_health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_static_css_served(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
