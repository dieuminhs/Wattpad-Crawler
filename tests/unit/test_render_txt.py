import json
from pathlib import Path

from wattpad_crawler.archive.repository import ArchiveRepository
from wattpad_crawler.models import Part, Story
from wattpad_crawler.render.txt import render_txt
from wattpad_crawler.scrape.chapter_html import ChapterContent


def test_render_txt_concatenates_chapters_in_order(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi" / "parts"
    sd.mkdir(parents=True)
    (sd / "01_100_one.txt").write_text("Chapter one body.")
    (sd / "02_101_two.txt").write_text("Chapter two body.")
    (sd.parent / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [
            {"part_id": "100", "ordinal": 1, "title": "One"},
            {"part_id": "101", "ordinal": 2, "title": "Two"},
        ],
    }))
    out = render_txt(sd.parent)
    assert "Chapter one body." in out
    assert "Chapter two body." in out
    assert out.index("Chapter one body.") < out.index("Chapter two body.")
    assert "One" in out  # chapter title appears


def test_render_txt_handles_missing_part_files(output_dir: Path):
    """If a chapter .txt file is missing, render proceeds without crashing."""
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "Missing"}],
    }))
    # No .txt files exist — should still render header but no body
    out = render_txt(sd)
    assert "Hi" in out  # title is in output
    assert "Missing" not in out  # chapter not in output (no body file)


def test_render_txt_writes_output_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    parts = sd / "parts"
    parts.mkdir(parents=True)
    (parts / "01_100_one.txt").write_text("Body.")
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    render_txt(sd)
    assert (sd / "output" / "hi.txt").exists()

def test_render_txt_remains_plain_without_export_preset_css(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    parts = sd / "parts"
    parts.mkdir(parents=True)
    (parts / "01_100_one.txt").write_text("Body.")
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))

    out = render_txt(sd)

    assert "Body." in out
    assert "export-preset" not in out
    assert "line-height" not in out


def test_render_txt_reads_archive_database(output_dir: Path):
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
            "<html/>",
            [],
            [],
        )
    repo.close()

    out = render_txt(sd)

    assert "DB body." in out


def test_render_txt_reads_compacted_archive_database(output_dir: Path):
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
                text="First\n\nSecond",
                paragraphs=[
                    {"id": "p1", "text": "First", "html": "<p>First</p>"},
                    {"id": "p2", "text": "Second", "html": "<p>Second</p>"},
                ],
                images=[],
            ),
            "<html/>",
            [],
            [],
        )
        repo.db.execute("UPDATE parts SET body_text = '', raw_html = '' WHERE part_id = '100'")
    repo.close()

    out = render_txt(sd)

    assert "First\n\nSecond" in out
