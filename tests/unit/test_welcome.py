import json
from pathlib import Path

from fastapi.testclient import TestClient

from wattpad_crawler.config import Config
from wattpad_crawler.web.app import build_app


def test_welcome_page_renders_guided_steps(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/welcome")

    assert r.status_code == 200
    assert "First-run setup" in r.text
    assert "Local personal archive" in r.text
    assert "Archive folder" in r.text
    assert "Wattpad access" in r.text
    assert str(output_dir) in r.text
    assert "Open cookie setup" in r.text
    assert "Archive your first story" in r.text


def test_welcome_page_shows_cookie_saved_state(output_dir: Path):
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/welcome")

    assert r.status_code == 200
    assert "Cookie saved" in r.text
    assert "Review cookie setup" in r.text


def test_dashboard_shows_welcome_cta_for_first_run(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/")

    assert r.status_code == 200
    assert "Finish setup" in r.text
    assert "Open welcome wizard" in r.text
    assert 'href="/welcome"' in r.text
    assert "Archive story" in r.text


def test_dashboard_keeps_archive_forms_when_welcome_cta_hidden(output_dir: Path):
    story_dir = output_dir / "stories" / "alice" / "42_story"
    story_dir.mkdir(parents=True)
    (story_dir / "metadata.json").write_text(
        json.dumps(
            {
                "story_id": "42",
                "title": "Story",
                "author_username": "alice",
                "tags": [],
                "description": "",
                "parts": [],
            }
        )
    )
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/")

    assert r.status_code == 200
    assert "Open welcome wizard" not in r.text
    assert "Archive library" in r.text
    assert "Archive story" in r.text
    assert "Archive list" in r.text
