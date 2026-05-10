import json
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from wattpad_crawler.archive.repository import ArchiveRepository
from wattpad_crawler.auth import AuthError
from wattpad_crawler.config import Config, load_config
from wattpad_crawler.models import Part, Story
from wattpad_crawler.scrape.chapter_html import ChapterContent
from wattpad_crawler.web import runner
from wattpad_crawler.web.app import build_app
from wattpad_crawler.web.routes import _save_cookie


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


def test_config_page_renders_current_settings(output_dir: Path):
    cfg = Config(output_dir=output_dir, rate_limit_per_sec=1.5, workers_per_story=4)
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/config")

    assert r.status_code == 200
    assert "Archive settings" in r.text
    assert 'name="rate_limit_per_sec"' in r.text
    assert 'value="1.5"' in r.text
    assert 'name="workers_per_story"' in r.text
    assert 'value="4"' in r.text


def test_config_post_saves_rate_limit_and_workers(output_dir: Path):
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    r = client.post(
        "/config",
        data={"rate_limit_per_sec": "3.5", "workers_per_story": "6"},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/config?saved=1"
    saved = load_config(output_dir)
    assert saved.cookie == "tok"
    assert saved.rate_limit_per_sec == 3.5
    assert saved.workers_per_story == 6


def test_pages_cache_bust_static_css(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/setup")
    assert r.status_code == 200
    assert 'href="/static/style.css?v=' in r.text


def test_setup_post_saves_cookie(output_dir: Path, monkeypatch):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    monkeypatch.setattr("wattpad_crawler.web.routes.validate_cookie", lambda c: None)
    r = client.post("/setup", data={"cookie": "tok-abc-123"}, follow_redirects=False)
    assert r.status_code in (200, 303)
    text = (output_dir / "_config.toml").read_text()
    assert "tok-abc-123" in text


def test_setup_post_strips_whitespace(output_dir: Path, monkeypatch):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    monkeypatch.setattr("wattpad_crawler.web.routes.validate_cookie", lambda c: None)
    client.post("/setup", data={"cookie": "  tok-abc-123  \n"}, follow_redirects=False)
    text = (output_dir / "_config.toml").read_text()
    assert 'cookie = "tok-abc-123"' in text


def test_save_cookie_escapes_toml_sensitive_characters(output_dir: Path):
    cookie = 'token-prefix"quoted\\tail'

    _save_cookie(output_dir, cookie)

    cfg = load_config(output_dir)
    assert cfg.cookie == cookie


def test_dashboard_renders(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "library" in r.text.lower()
    assert "story" in r.text.lower()


def test_base_page_renders_job_panel_shell(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/")

    assert r.status_code == 200
    assert 'id="job-panel"' in r.text
    assert 'id="job-panel-toggle"' in r.text
    assert 'id="job-panel-events"' in r.text
    assert "Open full" in r.text


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
    assert r.headers["location"].startswith("/?job_id=")
    job_id = r.headers["location"].split("=", 1)[1]
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
    assert r.headers["location"].startswith("/?job_id=")
    job_id = r.headers["location"].split("=", 1)[1]
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
    assert "Starting story: Hi" in r.text
    assert "<code>story.start</code>" not in r.text


def test_job_detail_progress_uses_friendly_messages(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    job = app.state.job_manager.create("archive_story", {"story_id": "42"})
    job.emit("part.start", {"ordinal": 2, "title": "Second"})
    job.emit("part.done", {"ordinal": 2, "inline_comments": 3, "end_comments": 1})
    job.emit("render.failed", {"format": "epub", "error": "boom"})
    client = TestClient(app)

    r = client.get(f"/jobs/{job.job_id}")

    assert r.status_code == 200
    assert "Reading chapter 2: Second" in r.text
    assert "Saved chapter 2 with 4 comments" in r.text
    assert "Could not build EPUB output" in r.text
    assert "<code>part.start</code>" not in r.text
    assert "<code>render.failed</code>" not in r.text


def test_job_summary_returns_all_retained_events(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    job = app.state.job_manager.create("archive_story", {"story_id": "42"})
    job.emit("part.start", {"ordinal": 1, "title": "One"})
    job.emit("part.done", {"ordinal": 1, "inline_comments": 2, "end_comments": 3})
    job.set_done()
    client = TestClient(app)

    r = client.get(f"/jobs/{job.job_id}/summary")

    assert r.status_code == 200
    assert r.json() == {
        "job_id": job.job_id,
        "kind": "archive_story",
        "args": {"story_id": "42"},
        "status": "done",
        "error": None,
        "next_seq": 2,
        "events": [
            {"kind": "part.start", "data": {"ordinal": 1, "title": "One"}, "seq": 1},
            {
                "kind": "part.done",
                "data": {"ordinal": 1, "inline_comments": 2, "end_comments": 3},
                "seq": 2,
            },
        ],
    }


def test_job_summary_unknown_returns_404(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/jobs/nonexistent/summary")

    assert r.status_code == 404


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


def test_library_empty(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library")
    assert r.status_code == 200
    assert "no stories" in r.text.lower() or "empty" in r.text.lower()


def test_library_lists_stories(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": ["x"], "description": "d", "parts": [],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library")
    assert r.status_code == 200
    assert "My Tale" in r.text
    assert "alice" in r.text
    assert "⋯" in r.text
    assert "Reset for refetch" in r.text
    assert "Remove from archive" in r.text
    assert "Bookmark" in r.text


def test_library_bookmark_story_toggles_flag(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(Story(story_id="42", title="My Tale", author_username="alice"))
    repo.close()
    app = build_app(cfg)
    client = TestClient(app)

    r = client.post("/library/bookmark/42", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/library?bookmarked=42"
    repo = ArchiveRepository(output_dir).connect()
    assert repo.get_story("42")["bookmarked"] is True
    repo.close()


def test_library_remove_story_deletes_db_and_story_folder(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(Story(story_id="42", title="My Tale", author_username="alice"))
    repo.close()
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text("{}")
    app = build_app(cfg)
    client = TestClient(app)

    r = client.post("/library/remove/42", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/library?removed=42"
    assert not sd.exists()
    repo = ArchiveRepository(output_dir).connect()
    assert repo.get_story("42") is None
    repo.close()


def test_library_remove_story_deletes_file_only_story_folder(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "MeoMupppp" / "383728013_edit-n-ng-gia-ti-u-phu-lang"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "383728013",
        "title": "Edit nàng gia tiểu phu lang",
        "author_username": "MeoMupppp",
        "tags": [],
        "parts": [],
    }))
    app = build_app(cfg)
    client = TestClient(app)

    r = client.post("/library/remove/383728013", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/library?removed=383728013"
    assert not sd.exists()


def test_library_reset_story_marks_parts_pending(output_dir: Path):
    from wattpad_crawler.archive.state import Manifest
    from wattpad_crawler.models import Part, Story

    cfg = Config(output_dir=output_dir)
    story = Story(
        story_id="42",
        title="My Tale",
        author_username="alice",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    m = Manifest(output_dir).connect()
    m.upsert_story(story)
    m.upsert_parts(story)
    m.set_story_status("42", "done")
    m.set_part_status("42", "100", "done", body_hash="abc")
    m.close()
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "", "parts": [],
    }))

    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/library/reset/42", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/library?reset=42"
    m = Manifest(output_dir).connect()
    assert m.get_story("42")["status"] == "pending"
    assert m.get_part("42", "100")["body_hash"] is None
    m.close()


def test_library_reset_story_marks_archive_database_pending(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    story = Story(
        story_id="42",
        title="My Tale",
        author_username="alice",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(story)
        repo.upsert_part(
            "42",
            story.parts[0],
            ChapterContent(text="Body", paragraphs=[], images=[]),
            "<html/>",
            [],
            [],
            body_hash="abc",
        )
    repo.close()

    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/library/reset/42", follow_redirects=False)

    assert r.status_code == 303
    repo = ArchiveRepository(output_dir).connect()
    assert repo.list_parts("42")[0]["status"] == "pending"
    repo.close()


def test_library_reset_missing_story_404(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/library/reset/missing", follow_redirects=False)
    assert r.status_code == 404


def test_library_cover_serves_when_present(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "T", "author_username": "alice",
        "tags": [], "parts": [],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library/cover/alice/42_my-tale")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")


def test_library_cover_404_when_missing(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library/cover/alice/nonexistent")
    assert r.status_code == 404


def test_library_cover_blocks_path_traversal(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library/cover/..%2F..%2F/x")
    assert r.status_code in (400, 404)


def test_reader_story_toc(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.txt").write_text("Body of chapter one.")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/42_my-tale")
    assert r.status_code == 200
    assert "My Tale" in r.text
    assert "One" in r.text


def test_reader_chapter_view(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.txt").write_text("Body of chapter one.")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/42_my-tale/1")
    assert r.status_code == 200
    assert "Body of chapter one." in r.text


def test_reader_chapter_view_reads_from_archive_database(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    story = Story(
        story_id="42",
        title="My Tale",
        author_username="alice",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    content = ChapterContent(
        text="Body from database.",
        paragraphs=[{"id": "p1", "text": "Body from database.", "html": "Body from database."}],
        images=[],
    )
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(story)
        repo.upsert_part(story.story_id, story.parts[0], content, "<html/>", [], [])
    repo.close()

    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/42_my-tale/1")

    assert r.status_code == 200
    assert "Body from database." in r.text


def test_reader_chapter_view_groups_inline_comments_by_paragraph(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.json").write_text(json.dumps({
        "part_id": "100",
        "ordinal": 1,
        "title": "One",
        "url": "https://w/100",
        "last_modified": None,
        "paragraphs": [
            {"id": "p1", "text": "First paragraph.", "html": "First paragraph."},
            {"id": "p2", "text": "Second paragraph.", "html": "Second paragraph."},
        ],
        "images": [],
    }))
    (sd / "parts" / "01_100_comments-inline.json").write_text(json.dumps([
        {
            "comment_id": "c1",
            "user": "bob",
            "body": "Inline on first.",
            "created_at": "t",
            "paragraph_id": "p1",
            "replies": [],
        },
        {
            "comment_id": "c2",
            "user": "carol",
            "body": "Inline on second.",
            "created_at": "t",
            "paragraph_id": "p2",
            "replies": [
                {
                    "comment_id": "r1",
                    "user": "dave",
                    "body": "Reply.",
                    "created_at": "t",
                    "paragraph_id": None,
                    "replies": [],
                }
            ],
        },
    ]))
    (sd / "parts" / "01_100_comments-end.json").write_text("[]")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    app = build_app(cfg)
    client = TestClient(app)

    r = client.get("/read/alice/42_my-tale/1")

    assert r.status_code == 200
    assert "First paragraph." in r.text
    assert "Second paragraph." in r.text
    assert 'class="comment-count-button"' in r.text
    assert 'data-comments-target="comments-p1"' in r.text
    assert 'class="comment-drawer" id="comments-p1" hidden aria-hidden="true"' in r.text
    assert "Hide comments" in r.text
    assert "bob" in r.text
    assert "Inline on first." in r.text
    assert "carol" in r.text
    assert "Inline on second." in r.text
    assert "dave" in r.text
    assert "Reply." in r.text


def test_reader_404_unknown_story(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/nonexistent")
    assert r.status_code == 404


def test_reader_path_traversal_blocked(output_dir: Path):
    """Path-traversal attempts via author or dir_name must be rejected."""
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/..%2F..%2F/42_x")
    assert r.status_code in (400, 404)


def test_artifact_download_epub(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "output").mkdir(parents=True)
    (sd / "output" / "my-tale.epub").write_bytes(b"PK\x03\x04fake-epub")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "", "parts": [],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library/output/alice/42_my-tale/epub")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/epub+zip"


def test_artifact_unknown_format_404(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "T", "author_username": "alice",
        "tags": [], "parts": [],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library/output/alice/42_my-tale/pdf")
    assert r.status_code == 404


# --- Phase 01 Plan 05 / REL-02: SSE handler + template integrated tests ---
# D-09 (after_seq query param rename) + D-10 (events.evicted gap announcement)
# These exercise the post-Plan-03 surface end-to-end via FastAPI's TestClient,
# proving the rename, the eviction-gap synthetic event shape, and the rendered
# template URL. The existing `output_dir` fixture comes from tests/conftest.py.


def _make_test_client(output_dir: Path) -> tuple[TestClient, "object"]:
    """Build a FastAPI app + TestClient for an empty archive directory.

    Returns (client, app) so tests can both drive HTTP and reach into
    app.state.job_manager to seed jobs and emit events directly.
    """
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    return TestClient(app), app


def test_job_stream_uses_after_seq_query_param_not_after(output_dir: Path):
    """D-09: SSE endpoint accepts ?after_seq=N. The legacy ?after=N is no
    longer a recognized parameter — FastAPI silently ignores unknown query
    params, so after_seq defaults to 0 (replay everything)."""
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager

    # Seed a job and immediately mark it done so the SSE generator terminates.
    job = mgr.create("archive_story", {"story_id": "1"})
    job.emit("part.start", {"part_id": "100"})
    job.emit("part.done", {"part_id": "100"})
    job.set_done()

    resp = client.get(f"/jobs/{job.job_id}/stream?after_seq=0", timeout=2.0)
    assert resp.status_code == 200
    body = resp.text
    assert "part.start" in body
    assert "part.done" in body
    assert "__status__" in body


def test_job_stream_each_event_payload_includes_seq_field(output_dir: Path):
    """REL-02 / D-07: every real SSE event JSON has a seq field alongside
    kind, data, ts — the new field shipped in Plan 03."""
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager
    job = mgr.create("archive_story", {})
    job.emit("a", {"x": 1})
    job.emit("b", {"x": 2})
    job.set_done()

    resp = client.get(f"/jobs/{job.job_id}/stream?after_seq=0", timeout=2.0)
    # SSE format: "data: <json>\n\n" lines.
    lines = [
        ln[len("data: "):]
        for ln in resp.text.splitlines()
        if ln.startswith("data: ")
    ]
    # First two are real events; third is __status__.
    ev_a = json.loads(lines[0])
    ev_b = json.loads(lines[1])
    assert ev_a["kind"] == "a"
    assert ev_a["seq"] == 1
    assert ev_b["kind"] == "b"
    assert ev_b["seq"] == 2


def test_job_stream_no_evicted_event_when_no_gap(output_dir: Path):
    """No eviction has happened — handler must NOT emit events.evicted."""
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager
    job = mgr.create("archive_story", {})
    for i in range(5):
        job.emit("tick", {"i": i})
    job.set_done()

    resp = client.get(f"/jobs/{job.job_id}/stream?after_seq=0", timeout=2.0)
    assert "events.evicted" not in resp.text


def test_job_stream_emits_evicted_event_on_gap(output_dir: Path, monkeypatch):
    """D-10: when after_seq < oldest_seq, emit synthetic events.evicted ahead
    of the snapshot with dropped_count / requested_after_seq /
    oldest_available_seq."""
    # Force a small deque cap so we can produce eviction with few events.
    monkeypatch.setattr(runner, "_MAX_EVENTS_PER_JOB", 5)
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager
    job = mgr.create("archive_story", {})
    # Emit 10 events; deque holds the last 5 (seqs 6..10); seqs 1..5 evicted.
    for i in range(10):
        job.emit("tick", {"i": i})
    job.set_done()

    # Client connects with after_seq=0; the gap is seqs 1..5 (5 dropped).
    resp = client.get(f"/jobs/{job.job_id}/stream?after_seq=0", timeout=2.0)
    body = resp.text
    assert "events.evicted" in body

    # The first SSE data line should be the events.evicted event.
    data_lines = [
        ln[len("data: "):]
        for ln in body.splitlines()
        if ln.startswith("data: ")
    ]
    first = json.loads(data_lines[0])
    assert first["kind"] == "events.evicted"
    # dropped_count = oldest_available_seq - 1 - requested_after_seq
    # oldest_available_seq is 6 (seqs 1..5 evicted); requested 0 -> 6-1-0 = 5.
    assert first["data"]["dropped_count"] == 5
    assert first["data"]["requested_after_seq"] == 0
    assert first["data"]["oldest_available_seq"] == 6
    assert "ts" in first


def test_job_stream_emits_evicted_only_once_per_stream(
    output_dir: Path, monkeypatch,
):
    """gap_announced flag prevents duplicate events.evicted within one
    stream. Even though the handler polls every 250ms, the second iteration
    must skip the gap check (gap_announced is True). Verified by counting
    events.evicted occurrences in the response body."""
    monkeypatch.setattr(runner, "_MAX_EVENTS_PER_JOB", 3)
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager
    job = mgr.create("archive_story", {})
    for i in range(10):
        job.emit("tick", {"i": i})
    job.set_done()

    resp = client.get(f"/jobs/{job.job_id}/stream?after_seq=0", timeout=2.0)
    # Count occurrences of the literal string in the body.
    assert resp.text.count("events.evicted") == 1


def test_job_stream_no_evicted_when_after_seq_advanced_past_gap(
    output_dir: Path, monkeypatch,
):
    """If client passes after_seq >= oldest_available_seq - 1, no gap exists."""
    monkeypatch.setattr(runner, "_MAX_EVENTS_PER_JOB", 5)
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager
    job = mgr.create("archive_story", {})
    for i in range(10):
        job.emit("tick", {"i": i})
    job.set_done()

    # oldest_seq is 6; after_seq=5 means client has consumed up to seq 5.
    # Gap check: 5 + 1 < 6 -> False; no events.evicted.
    resp = client.get(f"/jobs/{job.job_id}/stream?after_seq=5", timeout=2.0)
    assert "events.evicted" not in resp.text
    # Confirm we receive seqs 6..10.
    data_lines = [
        ln[len("data: "):]
        for ln in resp.text.splitlines()
        if ln.startswith("data: ") and "__status__" not in ln
    ]
    seqs = [json.loads(d)["seq"] for d in data_lines]
    assert seqs == [6, 7, 8, 9, 10]


def test_job_detail_template_renders_after_seq_url(output_dir: Path):
    """D-09 template change: rendered HTML for /jobs/{id} contains the
    new EventSource URL with ?after_seq=..., not the legacy ?after=..."""
    client, app = _make_test_client(output_dir)
    mgr = app.state.job_manager
    job = mgr.create("archive_story", {"story_id": "1"})
    # Emit one event so next_seq becomes 1; status remains pending so the
    # <script> block (which contains the EventSource URL) is rendered (the
    # template guards it on `job.status.value not in ("done", "failed")`).
    job.emit("a", {})

    resp = client.get(f"/jobs/{job.job_id}")
    assert resp.status_code == 200
    body = resp.text
    # New URL form is present.
    assert "?after_seq=" in body
    # The current next_seq (1) is rendered into the URL.
    assert "?after_seq=1" in body
    # Old form is gone.
    assert "?after=" not in body
    assert "job.events|length" not in body  # template syntax leaked = bug


# ---- AUTH-05 atomic-save tests (Phase 2 / Plan 05) ----


def test_save_cookie_uses_atomic_pattern(output_dir: Path, monkeypatch):
    """AUTH-05 / D-19: _save_cookie writes via tmp + os.replace, NOT a direct write."""
    output_dir.mkdir(parents=True, exist_ok=True)
    captured = {"call": None}
    real_replace = os.replace

    def fake_replace(src, dst):
        captured["call"] = (str(src), str(dst))
        # Do the real replace so the file ends up at config_path for any downstream check.
        real_replace(src, dst)

    monkeypatch.setattr("wattpad_crawler.web.routes.os.replace", fake_replace)

    _save_cookie(output_dir, "abc12345")

    assert captured["call"] is not None, "os.replace was not called — atomic pattern missing"
    src, dst = captured["call"]
    assert dst == str(output_dir / "_config.toml"), \
        f"Expected dst=_config.toml, got {dst!r}"
    # The src tmp file should match the _config.toml.{pid}.{tid}.tmp shape.
    assert "_config.toml." in src, f"Expected tmp path with _config.toml. prefix, got {src!r}"
    assert src.endswith(".tmp"), f"Expected tmp path ending .tmp, got {src!r}"


def test_save_cookie_crash_safe(output_dir: Path, monkeypatch):
    """AUTH-05 / ROADMAP success criterion #4: a crash between tmp write and
    os.replace leaves _config.toml either unchanged or fully written — never
    zero bytes or partial."""
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "_config.toml"
    original = 'cookie = "old-cookie-value"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n'
    config_path.write_text(original, encoding="utf-8")

    # Simulate a crash exactly between tmp.write_text() and os.replace().
    monkeypatch.setattr(
        "wattpad_crawler.web.routes.os.replace",
        lambda src, dst: (_ for _ in ()).throw(RuntimeError("simulated crash")),
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        _save_cookie(output_dir, "new-cookie-value")

    # _config.toml MUST still contain the original content — not zero bytes, not partial.
    after = config_path.read_text(encoding="utf-8")
    assert after == original, \
        f"_config.toml was mutated by failed save:\nBefore: {original!r}\nAfter: {after!r}"
    assert len(after) > 0, "_config.toml is zero bytes — crash was not safe"


def test_save_cookie_cleans_up_tmp_on_failure(output_dir: Path, monkeypatch):
    """AUTH-05 / D-19: tmp file cleanup on exception — no leftover *.tmp files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "_config.toml"
    config_path.write_text(
        'cookie = "old"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "wattpad_crawler.web.routes.os.replace",
        lambda src, dst: (_ for _ in ()).throw(RuntimeError("simulated crash")),
    )

    with pytest.raises(RuntimeError):
        _save_cookie(output_dir, "new-cookie-value")

    # No tmp files left behind in output_dir.
    leftover = list(output_dir.glob("_config.toml.*.tmp"))
    assert leftover == [], f"Tmp file cleanup failed; leftovers: {leftover}"


# ---- AUTH-03 /setup UX tests (Phase 2 / Plan 05) ----


def test_setup_post_invalid_cookie_rerenders(output_dir: Path, monkeypatch):
    """AUTH-03 / ROADMAP success criterion #2: invalid cookie POST re-renders 400 with
    error banner and does NOT modify _config.toml."""
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    config_path = output_dir / "_config.toml"
    original = 'cookie = "old-cookie"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n'
    config_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        "wattpad_crawler.web.routes.validate_cookie",
        lambda c: (_ for _ in ()).throw(AuthError("cookie rejected")),
    )

    resp = client.post("/setup", data={"cookie": "new-bad-cookie"})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    body_lower = resp.text.lower()
    assert "rejected" in body_lower, "Auth-error banner missing in response body"
    # _config.toml MUST be unchanged.
    assert config_path.read_text(encoding="utf-8") == original, \
        "_config.toml was modified despite validation failure"


def test_setup_post_valid_cookie_saves(output_dir: Path, monkeypatch):
    """AUTH-03 / D-12: valid cookie POST atomically saves and 303-redirects to /setup?saved=1."""
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    config_path = output_dir / "_config.toml"
    config_path.write_text(
        'cookie = "old"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "wattpad_crawler.web.routes.validate_cookie",
        lambda c: None,  # success
    )

    resp = client.post("/setup", data={"cookie": "new-good-cookie"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "/setup?saved=1" in resp.headers.get("location", "")
    after = config_path.read_text(encoding="utf-8")
    assert 'cookie = "new-good-cookie"' in after, \
        f"Cookie was not saved; file contents: {after!r}"


def test_setup_post_network_error(output_dir: Path, monkeypatch):
    """AUTH-03 / D-10: network error during validation renders banner with error_kind=network."""
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    monkeypatch.setattr(
        "wattpad_crawler.web.routes.validate_cookie",
        lambda c: (_ for _ in ()).throw(httpx.ConnectError("simulated DNS failure")),
    )

    resp = client.post("/setup", data={"cookie": "any-cookie"})
    assert resp.status_code == 400
    body = resp.text.lower()
    assert "could not reach" in body or "network" in body or "connection" in body, \
        "Network-error banner missing in response body"


def test_setup_post_shows_masked_attempted(output_dir: Path, monkeypatch):
    """AUTH-03 / D-11: on error, attempted_cookie_masked is rendered back to the user."""
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)

    monkeypatch.setattr(
        "wattpad_crawler.web.routes.validate_cookie",
        lambda c: (_ for _ in ()).throw(AuthError("rejected")),
    )

    submitted = "AbCdEfGh12345678"  # length > 8 so _mask returns "AbCd…5678"
    resp = client.post("/setup", data={"cookie": submitted})
    assert resp.status_code == 400
    # _mask("AbCdEfGh12345678") == "AbCd…5678" (4-char prefix + ellipsis + 4-char suffix).
    expected_mask = "AbCd…5678"
    assert expected_mask in resp.text, \
        f"Expected masked cookie {expected_mask!r} in response body; got: {resp.text[:500]!r}"
