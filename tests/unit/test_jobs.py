from pathlib import Path
from unittest.mock import MagicMock

from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.config import Config
from wattpad_crawler.jobs import JobDeps, archive_story
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
