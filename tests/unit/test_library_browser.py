import json
from pathlib import Path

from wattpad_crawler.web.library_browser import LibraryEntry, scan_library


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
