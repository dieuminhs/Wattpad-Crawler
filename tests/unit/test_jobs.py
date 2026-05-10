import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.auth import AuthFailedError
from wattpad_crawler.config import Config
from wattpad_crawler.jobs import (
    JobDeps,
    RenderError,
    ResolveError,
    archive_many,
    archive_story,
    fetch_full_chapter_html,
    resolve_story_id,
    resolve_url_story_id,
)
from wattpad_crawler.models import Part, Story
from wattpad_crawler.scrape.chapter_html import ChapterContent


def _make_deps(story: Story) -> JobDeps:
    return JobDeps(
        fetch_story=MagicMock(return_value=story),
        fetch_chapter_html=MagicMock(return_value="<pre>One body.</pre>"),
        parse_chapter=MagicMock(return_value=ChapterContent(
            text="One body.",
            paragraphs=[{"id": "p1", "text": "One body.", "html": "One body."}],
            images=[],
        )),
        fetch_inline_comments=MagicMock(return_value=[]),
        fetch_end_comments=MagicMock(return_value=[]),
        fetch_cover_bytes=MagicMock(return_value=b""),
    )


def test_archive_story_writes_all_artifacts(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    archive_story(cfg, fake_client, manifest, "42", deps=deps)

    sd = output_dir / "stories" / "bob" / "42_hi"
    assert (sd / "metadata.json").exists()
    assert (sd / "parts" / "01_100_one.json").exists()
    assert (sd / "parts" / "01_100_one.txt").exists()
    assert (sd / "output").exists()
    row = manifest.get_part("42", "100")
    assert row["status"] == "done"
    manifest.close()


def test_archive_story_skips_already_done_parts(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    # First run
    archive_story(cfg, fake_client, manifest, "42", deps=deps)
    fetch_call_count_after_first = deps.fetch_chapter_html.call_count

    # Second run — part is already 'done', should not refetch chapter HTML
    archive_story(cfg, fake_client, manifest, "42", deps=deps)
    assert deps.fetch_chapter_html.call_count == fetch_call_count_after_first
    manifest.close()


def test_archive_story_marks_failed_on_chapter_error(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    deps.fetch_chapter_html.side_effect = RuntimeError("network exploded")

    archive_story(cfg, fake_client, manifest, "42", deps=deps)
    row = manifest.get_part("42", "100")
    assert row["status"] == "failed"
    assert row["last_error"] is not None
    assert "network exploded" in row["last_error"]
    manifest.close()


def test_archive_story_keeps_part_when_comment_fetch_fails(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    request = httpx.Request("GET", "https://www.wattpad.com/api/v3/parts/100/comments")
    response = httpx.Response(400, request=request)
    deps.fetch_inline_comments.side_effect = httpx.HTTPStatusError(
        "400 Bad Request",
        request=request,
        response=response,
    )
    events: list[tuple[str, dict]] = []

    archive_story(
        cfg,
        fake_client,
        manifest,
        "42",
        deps=deps,
        progress=lambda kind, data: events.append((kind, data)),
    )

    row = manifest.get_part("42", "100")
    assert row["status"] == "done"
    sd = output_dir / "stories" / "bob" / "42_hi" / "parts"
    assert json.loads((sd / "01_100_comments-inline.json").read_text()) == []
    assert "comments.failed" in [kind for kind, _ in events]
    manifest.close()


def test_fetch_full_chapter_html_appends_storytext_pages():
    responses = {
        "https://www.wattpad.com/1495181769-chapter-title": "<p>Page one.</p>",
        "https://www.wattpad.com/apiv2/": "<p>Page two.</p>",
    }
    client = MagicMock()
    client.get.side_effect = [
        MagicMock(text=responses["https://www.wattpad.com/1495181769-chapter-title"]),
        MagicMock(text=responses["https://www.wattpad.com/apiv2/"]),
        MagicMock(text=""),
    ]

    html = fetch_full_chapter_html(
        client,
        "https://www.wattpad.com/1495181769-chapter-title",
    )

    assert html == "<p>Page one.</p>\n<p>Page two.</p>"
    client.get.assert_any_call("https://www.wattpad.com/1495181769-chapter-title")
    client.get.assert_any_call(
        "https://www.wattpad.com/apiv2/",
        params={"m": "storytext", "id": "1495181769", "page": 2},
        headers={
            "Accept": "text/html, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.wattpad.com/1495181769-chapter-title",
        },
    )


def test_fetch_full_chapter_html_returns_parseable_combined_fragments():
    from wattpad_crawler.scrape.chapter_html import extract_chapter

    client = MagicMock()
    client.get.side_effect = [
        MagicMock(text="""
            <html><body><main class="page-container">
              <p data-p-id="p1">Page one.</p>
            </main></body></html>
        """),
        MagicMock(text='<p data-p-id="p2">Page two.</p>'),
        MagicMock(text=""),
    ]

    html = fetch_full_chapter_html(client, "https://www.wattpad.com/1495181769-title")
    content = extract_chapter(html)

    assert [p["text"] for p in content.paragraphs] == ["Page one.", "Page two."]


def test_archive_story_isolates_cover_fetch_failure(output_dir: Path):
    """A custom JobDeps whose fetch_cover_bytes raises must not crash the job."""
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        cover_url="https://example.com/c.jpg",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    deps.fetch_cover_bytes.side_effect = RuntimeError("CDN exploded")

    # Must not raise
    archive_story(cfg, fake_client, manifest, "42", deps=deps)

    # Story still archived: metadata + at least one part
    sd = output_dir / "stories" / "bob" / "42_hi"
    assert (sd / "metadata.json").exists()
    assert manifest.get_part("42", "100")["status"] == "done"
    manifest.close()


def test_archive_story_renderers_are_independent(output_dir: Path, monkeypatch):
    """If render_txt fails, render_html and render_epub still run."""
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    # Patch render_txt to raise; render_html and render_epub still need to be called.
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    txt_mock = MagicMock(side_effect=RuntimeError("txt rendering broke"))
    html_mock = MagicMock()
    epub_mock = MagicMock()
    monkeypatch.setattr(render_txt_mod, "render_txt", txt_mock)
    monkeypatch.setattr(render_html_mod, "render_html", html_mock)
    monkeypatch.setattr(render_epub_mod, "render_epub", epub_mock)

    archive_story(cfg, fake_client, manifest, "42", deps=deps)

    # All three renderers were called, even though txt raised
    assert txt_mock.called
    assert html_mock.called
    assert epub_mock.called
    manifest.close()


def test_resolve_numeric_id():
    assert resolve_story_id("123456789") == "123456789"


def test_resolve_story_url():
    assert resolve_story_id("https://www.wattpad.com/story/123456-some-title") == "123456"


def test_resolve_story_url_no_slug():
    assert resolve_story_id("https://www.wattpad.com/story/789") == "789"


def test_resolve_part_url_to_story_requires_lookup():
    """Part URLs need an API call; this pure helper rejects them."""
    with pytest.raises(ResolveError):
        resolve_story_id("https://www.wattpad.com/1001-chapter-one")


def test_resolve_url_story_id_fetches_parent_story_for_part_url(monkeypatch):
    seen = {}

    def fake_fetch_part_story_id(client, part_id):
        seen["client"] = client
        seen["part_id"] = part_id
        return "123456789"

    monkeypatch.setattr(
        "wattpad_crawler.jobs.api_story.fetch_part_story_id",
        fake_fetch_part_story_id,
    )

    client = object()
    assert resolve_url_story_id(client, "https://www.wattpad.com/1001-chapter-one") == "123456789"
    assert seen == {"client": client, "part_id": "1001"}


def test_resolve_garbage_input():
    with pytest.raises(ResolveError):
        resolve_story_id("not a url or id")


def test_archive_many_collects_results(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    fake_client = MagicMock()

    # First story succeeds, second raises during fetch
    s1 = Story(story_id="1", title="A", author_username="x", parts=[])

    # Use a single deps that behaves differently per story_id
    def fetch_story(client, sid):
        if sid == "fail":
            raise RuntimeError("boom")
        return Story(story_id=sid, title=f"S{sid}", author_username="x", parts=[])

    combined = _make_deps(s1)
    combined.fetch_story = fetch_story

    results = archive_many(cfg, fake_client, manifest, ["1", "fail", "2"], deps=combined)
    assert results["1"] == "done"
    assert results["fail"].startswith("failed:")
    assert results["2"] == "done"
    manifest.close()


def test_archive_story_emits_progress_events(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    events: list[tuple[str, dict]] = []

    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps,
        progress=lambda kind, data: events.append((kind, data)),
    )

    kinds = [k for k, _ in events]
    assert "story.start" in kinds
    assert "part.start" in kinds
    assert "part.done" in kinds
    assert "story.done" in kinds
    part_start = next(d for k, d in events if k == "part.start")
    assert part_start["part_id"] == "100"
    assert part_start["ordinal"] == 1
    manifest.close()


def test_archive_story_emits_part_failed_on_error(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    deps.fetch_chapter_html.side_effect = RuntimeError("bad")
    events: list[tuple[str, dict]] = []

    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps,
        progress=lambda kind, data: events.append((kind, data)),
    )

    kinds = [k for k, _ in events]
    assert "part.failed" in kinds
    failed = next(d for k, d in events if k == "part.failed")
    assert "bad" in failed["error"]
    manifest.close()


def test_archive_story_progress_default_is_noop(output_dir: Path):
    """Calling without a progress callback must still work (CLI path)."""
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(story_id="42", title="Hi", author_username="bob",
                  parts=[Part(part_id="100", ordinal=1, title="One", url="https://w")])
    fake_client = MagicMock()
    deps = _make_deps(story)
    archive_story(cfg, fake_client, manifest, "42", deps=deps)  # no progress=
    manifest.close()


# --- Phase 1 REL-04 render-failure tests ---


def test_render_error_is_exception_subclass():
    assert issubclass(RenderError, Exception)
    e = RenderError("msg")
    assert str(e) == "msg"


def test_archive_story_raises_render_error_when_all_renderers_fail(
    output_dir, monkeypatch,
):
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    monkeypatch.setattr(
        render_txt_mod, "render_txt",
        MagicMock(side_effect=RuntimeError("txt fail")),
    )
    monkeypatch.setattr(
        render_html_mod, "render_html",
        MagicMock(side_effect=RuntimeError("html fail")),
    )
    monkeypatch.setattr(
        render_epub_mod, "render_epub",
        MagicMock(side_effect=RuntimeError("epub fail")),
    )

    events: list[tuple[str, dict]] = []

    def progress(kind, data):
        events.append((kind, dict(data)))

    with pytest.raises(RenderError) as exc_info:
        archive_story(
            cfg, fake_client, manifest, "42",
            deps=deps, progress=progress,
        )

    # Error message names all three formats (via the render_status dict repr).
    msg = str(exc_info.value)
    assert "txt" in msg
    assert "html" in msg
    assert "epub" in msg
    assert "failed" in msg

    # story.done was emitted BEFORE the raise (D-15 step 3) with
    # render_status reflecting all three failures.
    done_events = [d for k, d in events if k == "story.done"]
    assert len(done_events) == 1
    assert done_events[0]["render_status"] == {
        "txt": "failed", "html": "failed", "epub": "failed",
    }

    # Three render.failed events (one per format).
    failed_events = [d for k, d in events if k == "render.failed"]
    assert len(failed_events) == 3
    assert {d["format"] for d in failed_events} == {"txt", "html", "epub"}

    manifest.close()


def test_archive_story_partial_render_failure_does_not_raise(
    output_dir, monkeypatch,
):
    """Two failed + one ok = partial. story.done emits the breakdown, no RenderError."""
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    # txt succeeds, html and epub fail.
    monkeypatch.setattr(render_txt_mod, "render_txt", MagicMock())
    monkeypatch.setattr(
        render_html_mod, "render_html",
        MagicMock(side_effect=RuntimeError("html fail")),
    )
    monkeypatch.setattr(
        render_epub_mod, "render_epub",
        MagicMock(side_effect=RuntimeError("epub fail")),
    )

    events: list[tuple[str, dict]] = []

    def progress(kind, data):
        events.append((kind, dict(data)))

    # Must NOT raise.
    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps, progress=progress,
    )

    done_events = [d for k, d in events if k == "story.done"]
    assert len(done_events) == 1
    assert done_events[0]["render_status"] == {
        "txt": "ok", "html": "failed", "epub": "failed",
    }

    # Two render.failed events, none for txt.
    failed_events = [d for k, d in events if k == "render.failed"]
    assert {d["format"] for d in failed_events} == {"html", "epub"}

    manifest.close()


def test_archive_story_all_ok_emits_render_status_all_ok(
    output_dir, monkeypatch,
):
    """Sanity: all renderers succeed -> render_status all 'ok', no RenderError."""
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    monkeypatch.setattr(render_txt_mod, "render_txt", MagicMock())
    monkeypatch.setattr(render_html_mod, "render_html", MagicMock())
    monkeypatch.setattr(render_epub_mod, "render_epub", MagicMock())

    events: list[tuple[str, dict]] = []

    def progress(kind, data):
        events.append((kind, dict(data)))

    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps, progress=progress,
    )

    done_events = [d for k, d in events if k == "story.done"]
    assert len(done_events) == 1
    assert done_events[0]["render_status"] == {
        "txt": "ok", "html": "ok", "epub": "ok",
    }
    # No render.failed events.
    assert not any(k == "render.failed" for k, _ in events)

    manifest.close()


def test_archive_story_renderers_run_independently_when_one_fails(
    output_dir, monkeypatch,
):
    """A renderer raising must not skip subsequent renderers in the loop.

    Verifies D-15: all three renderers run unconditionally — txt's failure
    does not prevent html.render_html or epub.render_epub from being called.
    """
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    txt_mock = MagicMock(side_effect=RuntimeError("txt fail"))
    html_mock = MagicMock()
    epub_mock = MagicMock()
    monkeypatch.setattr(render_txt_mod, "render_txt", txt_mock)
    monkeypatch.setattr(render_html_mod, "render_html", html_mock)
    monkeypatch.setattr(render_epub_mod, "render_epub", epub_mock)

    archive_story(cfg, fake_client, manifest, "42", deps=deps)

    # All three were called despite txt's failure.
    assert txt_mock.call_count == 1
    assert html_mock.call_count == 1
    assert epub_mock.call_count == 1

    manifest.close()


def test_archive_many_records_render_error_in_results(
    output_dir, monkeypatch,
):
    """archive_many's existing per-story exception handler catches RenderError
    and records 'failed: all renders failed: ...' in the results dict."""
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    monkeypatch.setattr(
        render_txt_mod, "render_txt",
        MagicMock(side_effect=RuntimeError("txt fail")),
    )
    monkeypatch.setattr(
        render_html_mod, "render_html",
        MagicMock(side_effect=RuntimeError("html fail")),
    )
    monkeypatch.setattr(
        render_epub_mod, "render_epub",
        MagicMock(side_effect=RuntimeError("epub fail")),
    )

    results = archive_many(cfg, fake_client, manifest, ["42"], deps=deps)
    assert "42" in results
    assert "failed" in results["42"]
    assert "all renders failed" in results["42"]

    manifest.close()


# ---- AUTH-04 tests (Phase 2 / Plan 04) ----


def test_archive_story_propagates_auth_failed(output_dir: Path):
    """AUTH-04 / D-16: archive_story re-raises AuthFailedError instead of swallowing."""
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    try:
        story = Story(
            story_id="42", title="Hi", author_username="bob",
            parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
        )
        fake_client = MagicMock()
        deps = _make_deps(story)
        deps.fetch_chapter_html.side_effect = AuthFailedError(
            "Wattpad returned HTTP 401 for https://w/100",
            status_code=401,
            url="https://w/100",
        )
        with pytest.raises(AuthFailedError):
            archive_story(cfg, fake_client, manifest, "42", deps=deps)
        # Manifest should reflect the failure, not "done".
        row = manifest.get_part("42", "100")
        assert row["status"] != "done", f"Expected status != 'done', got {row['status']!r}"
    finally:
        manifest.close()


def test_archive_story_emits_auth_failed_event(output_dir: Path):
    """AUTH-04 / D-17: archive_story emits auth.failed event BEFORE re-raising."""
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    try:
        story = Story(
            story_id="42", title="Hi", author_username="bob",
            parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
        )
        fake_client = MagicMock()
        deps = _make_deps(story)
        deps.fetch_chapter_html.side_effect = AuthFailedError(
            "Wattpad returned HTTP 401 for https://w/100",
            status_code=401,
            url="https://w/100",
        )
        events: list[tuple[str, dict]] = []

        def collect(kind: str, data: dict) -> None:
            events.append((kind, data))

        with pytest.raises(AuthFailedError):
            archive_story(cfg, fake_client, manifest, "42", deps=deps, progress=collect)
        # The auth.failed event must have been emitted BEFORE the re-raise.
        auth_events = [(k, d) for (k, d) in events if k == "auth.failed"]
        n = len(auth_events)
        assert n == 1, f"Expected exactly 1 auth.failed event, got {n}"
        kind, data = auth_events[0]
        assert data["part_id"] == "100"
        assert data["status_code"] == 401
        assert data["url"] == "https://w/100"
        assert "401" in data["message"]
    finally:
        manifest.close()
