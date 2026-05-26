import json
from pathlib import Path

from local_story_archive.archive.repository import ArchiveRepository
from local_story_archive.models import Part, Story
from local_story_archive.scrape.chapter_html import ChapterContent
from local_story_archive.web.library_browser import LibraryEntry, scan_library


def test_scan_library_empty(output_dir: Path):
    assert scan_library(output_dir) == []


def test_scan_library_one_story(output_dir: Path):
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42",
        "title": "My Tale",
        "author_username": "alice",
        "tags": ["fantasy"],
        "description": "d",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    out = scan_library(output_dir)
    assert len(out) == 1
    e = out[0]
    assert isinstance(e, LibraryEntry)
    assert e.story_id == "42"
    assert e.title == "My Tale"
    assert e.author == "alice"
    assert e.tags == ["fantasy"]
    assert e.parts_count == 1
    assert e.dir_name == "42_my-tale"
    assert e.has_cover is False


def test_scan_library_prefers_archive_database(output_dir: Path):
    repo = ArchiveRepository(output_dir).connect()
    with repo.transaction():
        repo.upsert_story(Story(story_id="42", title="My Tale", author_username="alice"))
    repo.close()

    out = scan_library(output_dir)

    assert len(out) == 1
    assert out[0].story_id == "42"
    assert out[0].title == "My Tale"
    assert out[0].dir_name == "42_my-tale"

def test_scan_library_database_archive_does_not_require_legacy_part_files(output_dir: Path):
    repo = ArchiveRepository(output_dir).connect()
    story = Story(
        story_id="42",
        title="My Tale",
        author_username="alice",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://example.invalid/1")],
    )
    content = ChapterContent(
        text="chapter text",
        paragraphs=[{"id": "p1", "text": "chapter text", "html": "<p>chapter text</p>"}],
        images=[],
    )
    with repo.transaction():
        repo.upsert_story(story)
        repo.upsert_part(story.story_id, story.parts[0], content, "<p>chapter text</p>", [], [])
    repo.close()

    [entry] = scan_library(output_dir)

    assert entry.health_status != "broken"
    assert not any("parts folder is missing" in issue for issue in entry.health_issues or [])


def test_scan_library_detects_cover(output_dir: Path):
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "cover.jpg").write_bytes(b"x")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "T", "author_username": "alice",
        "tags": [], "parts": [],
    }))
    [e] = scan_library(output_dir)
    assert e.has_cover is True


def test_scan_library_skips_dirs_without_metadata(output_dir: Path):
    """Directories that look like story dirs but have no metadata.json are skipped."""
    (output_dir / "stories" / "alice" / "42_x" / "parts").mkdir(parents=True)
    out = scan_library(output_dir)
    assert out == []


def test_scan_library_sorts_by_author_then_title(output_dir: Path):
    for author, story_id, title in [
        ("zelda", "1", "First"),
        ("alice", "2", "Second"),
        ("alice", "3", "Aardvark"),
    ]:
        sd = output_dir / "stories" / author / f"{story_id}_x"
        sd.mkdir(parents=True)
        (sd / "metadata.json").write_text(json.dumps({
            "story_id": story_id, "title": title, "author_username": author,
            "tags": [], "parts": [],
        }))
    titles = [(e.author, e.title) for e in scan_library(output_dir)]
    assert titles == [("alice", "Aardvark"), ("alice", "Second"), ("zelda", "First")]


def test_scan_library_handles_corrupt_metadata(output_dir: Path):
    """Don't crash on malformed metadata.json — skip it."""
    sd = output_dir / "stories" / "alice" / "42_x"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text("not valid json{{{")
    assert scan_library(output_dir) == []
