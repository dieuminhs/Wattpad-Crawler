from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.config import Config
from wattpad_crawler.jobs import (
    JobDeps,
    ResolveError,
    archive_many,
    archive_story,
    resolve_story_id,
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
    """Part URLs need an API call; this fn just rejects them."""
    with pytest.raises(ResolveError):
        resolve_story_id("https://www.wattpad.com/1001-chapter-one")


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
