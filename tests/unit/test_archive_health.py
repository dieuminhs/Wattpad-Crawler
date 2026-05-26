import json
from pathlib import Path

from local_story_archive.archive.health import check_story_archive
from local_story_archive.web.library_browser import scan_library


def _write_metadata(story_dir: Path, *, cover_url: str = "") -> dict:
    metadata = {
        "story_id": "42",
        "title": "My Tale",
        "author_username": "alice",
        "description": "d",
        "cover_url": cover_url,
        "tags": [],
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }
    story_dir.mkdir(parents=True)
    (story_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def test_check_story_archive_reports_complete_archive(output_dir: Path):
    story_dir = output_dir / "stories" / "alice" / "42_my-tale"
    metadata = _write_metadata(story_dir)
    parts_dir = story_dir / "parts"
    output = story_dir / "output"
    parts_dir.mkdir()
    output.mkdir()
    for suffix in ("json", "html", "txt"):
        (parts_dir / f"01_100_one.{suffix}").write_text("ok", encoding="utf-8")
    (parts_dir / "01_100_comments-inline.json").write_text("[]", encoding="utf-8")
    (parts_dir / "01_100_comments-end.json").write_text("[]", encoding="utf-8")
    (output / "My Tale.epub").write_bytes(b"epub")

    health = check_story_archive(story_dir, metadata)

    assert health.status == "ok"
    assert health.summary == "Archive complete"
    assert health.issues == []


def test_scan_library_attaches_broken_archive_health(output_dir: Path):
    story_dir = output_dir / "stories" / "alice" / "42_my-tale"
    _write_metadata(story_dir, cover_url="https://example.invalid/cover.jpg")

    [entry] = scan_library(output_dir)

    assert entry.health_status == "broken"
    assert entry.health_summary == "1 repair needed"
    assert any("parts folder is missing" in issue for issue in entry.health_issues or [])
    assert any("cover image missing" in issue for issue in entry.health_issues or [])
