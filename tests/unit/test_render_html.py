import json
from pathlib import Path

from local_story_archive.archive.repository import ArchiveRepository
from local_story_archive.models import Part, Story
from local_story_archive.render.html import render_html
from local_story_archive.scrape.chapter_html import ChapterContent


def test_render_html_single_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.html").write_text(
        '<pre data-p-id="x">First.</pre><pre data-p-id="y">Second.</pre>'
    )
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "Chapter One"}],
    }))
    out = render_html(sd)
    assert "<title>Hi</title>" in out
    assert "Chapter One" in out
    assert "First." in out
    assert "Second." in out


def test_render_html_escapes_title(output_dir: Path):
    """Story title with HTML special chars must be escaped in <title>."""
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "<script>alert(1)</script>",
        "author_username": "bob",
        "parts": [],
    }))
    out = render_html(sd)
    assert "<title>&lt;script&gt;alert(1)&lt;/script&gt;</title>" in out
    assert "<script>alert(1)</script>" not in out  # never raw


def test_render_html_writes_output_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "parts": [],
    }))
    render_html(sd)
    assert (sd / "output" / "hi.html").exists()

def test_render_html_includes_export_preset_css(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "parts": [],
    }))

    out = render_html(sd, export_preset="cozy")

    assert "--export-preset:cozy" in out
    assert "line-height:1.85" in out


def test_render_html_reads_archive_database(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    story = Story(
        story_id="42",
        title="Hi",
        author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(story)
        repo.upsert_part(
            "42",
            story.parts[0],
            ChapterContent(text="DB body.", paragraphs=[], images=[]),
            "<p>DB html.</p>",
            [],
            [],
        )
    repo.close()

    out = render_html(sd)

    assert "DB html." in out

def test_render_html_reads_compacted_archive_database(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    story = Story(
        story_id="42",
        title="Hi",
        author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(story)
        repo.upsert_part(
            "42",
            story.parts[0],
            ChapterContent(
                text="DB body.",
                paragraphs=[{"id": "p1", "text": "DB body.", "html": "<p>DB body.</p>"}],
                images=[],
            ),
            "<p>DB html.</p>",
            [],
            [],
        )
        repo.db.execute("UPDATE parts SET body_text = '', raw_html = '' WHERE part_id = '100'")
    repo.close()

    out = render_html(sd)

    assert "<p>DB body.</p>" in out
