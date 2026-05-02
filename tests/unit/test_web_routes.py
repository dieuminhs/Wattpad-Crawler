import time
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


def test_setup_page_renders(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/setup")
    assert r.status_code == 200
    assert "cookie" in r.text.lower()
    assert "wattpad" in r.text.lower()


def test_setup_post_saves_cookie(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/setup", data={"cookie": "tok-abc-123"}, follow_redirects=False)
    assert r.status_code in (200, 303)
    text = (output_dir / "_config.toml").read_text()
    assert "tok-abc-123" in text


def test_setup_post_strips_whitespace(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    client.post("/setup", data={"cookie": "  tok-abc-123  \n"}, follow_redirects=False)
    text = (output_dir / "_config.toml").read_text()
    assert 'cookie = "tok-abc-123"' in text


def test_dashboard_renders(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "library" in r.text.lower()
    assert "story" in r.text.lower()


def test_post_jobs_story_creates_and_starts(output_dir: Path, monkeypatch):
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    captured = {}

    def fake_archive_story(cfg_arg, _client, _manifest, sid, *, deps=None, progress=None):
        captured["sid"] = sid
        if progress:
            progress("story.start", {"story_id": sid})

    monkeypatch.setattr("wattpad_crawler.web.routes.archive_story", fake_archive_story)
    r = client.post("/jobs", data={"kind": "story", "target": "12345"}, follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 2.0
    job = app.state.job_manager.get(job_id)
    while job.status.value in ("pending", "running"):
        if time.monotonic() > deadline:
            raise AssertionError(f"job stuck at {job.status}")
        time.sleep(0.01)
    assert captured["sid"] == "12345"


def test_post_jobs_url_resolves(output_dir: Path, monkeypatch):
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    captured = {}

    def fake_archive_story(cfg_arg, _client, _manifest, sid, *, deps=None, progress=None):
        captured["sid"] = sid

    monkeypatch.setattr("wattpad_crawler.web.routes.archive_story", fake_archive_story)
    r = client.post("/jobs", data={
        "kind": "story",
        "target": "https://www.wattpad.com/story/789-foo-bar",
    }, follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 2.0
    job = app.state.job_manager.get(job_id)
    while job.status.value in ("pending", "running"):
        if time.monotonic() > deadline:
            raise AssertionError("job stuck")
        time.sleep(0.01)
    assert captured["sid"] == "789"


def test_post_jobs_invalid_kind_returns_400(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/jobs", data={"kind": "garbage"}, follow_redirects=False)
    assert r.status_code == 400


def test_job_detail_page(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    job = app.state.job_manager.create("archive_story", {"story_id": "42"})
    job.emit("story.start", {"story_id": "42", "title": "Hi"})
    client = TestClient(app)
    r = client.get(f"/jobs/{job.job_id}")
    assert r.status_code == 200
    assert "42" in r.text
    assert "story.start" in r.text


def test_job_detail_unknown_returns_404(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/jobs/nonexistent")
    assert r.status_code == 404


def test_sse_stream_replays_existing_events(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    job = app.state.job_manager.create("test", {})
    job.emit("test.tick", {"n": 1})
    job.emit("test.tick", {"n": 2})
    job.set_done()
    client = TestClient(app)
    with client.stream("GET", f"/jobs/{job.job_id}/stream?after=0") as r:
        assert r.status_code == 200
        text = "".join(chunk for chunk in r.iter_text())
    assert "test.tick" in text
    assert '"n": 1' in text or '"n":1' in text


def test_sse_stream_404_unknown_job(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/jobs/missing/stream")
    assert r.status_code == 404
