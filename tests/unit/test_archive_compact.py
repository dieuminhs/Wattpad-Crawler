import sqlite3
from pathlib import Path

from local_story_archive.archive.compact import compact_archive
from local_story_archive.cli import build_parser


def _create_archive_db(output_dir: Path) -> None:
    conn = sqlite3.connect(output_dir / "archive.sqlite")
    try:
        conn.executescript(
            """
            CREATE TABLE parts (
                story_id TEXT NOT NULL,
                part_id TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL,
                body_text TEXT NOT NULL DEFAULT '',
                raw_html TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE paragraphs (
                part_id TEXT NOT NULL,
                paragraph_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                html TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO parts(story_id, part_id, ordinal, body_text, raw_html)
            VALUES ('42', '100', 1, 'chapter text', '<p>chapter</p>')
            """
        )
        conn.execute(
            """
            INSERT INTO paragraphs(part_id, paragraph_id, ordinal, text, html)
            VALUES ('100', 'p1', 1, 'chapter text', '<p>chapter</p>')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _create_story_files(output_dir: Path) -> dict[str, Path]:
    story_dir = output_dir / "stories" / "alice" / "42_my-tale"
    parts_dir = story_dir / "parts"
    output = story_dir / "output"
    parts_dir.mkdir(parents=True)
    output.mkdir()
    paths = {
        "html": parts_dir / "01_100_one.html",
        "txt": parts_dir / "01_100_one.txt",
        "json": parts_dir / "01_100_one.json",
        "inline_comments": parts_dir / "01_100_comments-inline.json",
        "end_comments": parts_dir / "01_100_comments-end.json",
        "epub": output / "my-tale.epub",
        "metadata": story_dir / "metadata.json",
        "cover": story_dir / "cover.jpg",
    }
    for path in paths.values():
        path.write_text("x", encoding="utf-8")
    return paths


def test_compact_archive_dry_run_keeps_files(output_dir: Path):
    _create_archive_db(output_dir)
    paths = _create_story_files(output_dir)

    result = compact_archive(output_dir)

    assert result.files_removed == 6
    assert result.db_bytes_removed == 38
    assert result.bytes_removed == 44
    assert all(path.exists() for path in paths.values())


def test_compact_archive_apply_removes_only_redundant_files(output_dir: Path):
    _create_archive_db(output_dir)
    paths = _create_story_files(output_dir)

    result = compact_archive(output_dir, dry_run=False)

    assert result.files_removed == 6
    assert result.db_bytes_removed == 38
    for key in ("html", "txt", "json", "inline_comments", "end_comments", "epub"):
        assert not paths[key].exists()
    assert paths["metadata"].exists()
    assert paths["cover"].exists()
    conn = sqlite3.connect(output_dir / "archive.sqlite")
    try:
        row = conn.execute(
            """
            SELECT parts.body_text, parts.raw_html, paragraphs.text
            FROM parts
            JOIN paragraphs ON paragraphs.part_id = parts.part_id
            WHERE parts.part_id = '100'
            """
        ).fetchone()
    finally:
        conn.close()
    assert row == ("", "", "")


def test_compact_archive_skips_file_only_archives(output_dir: Path):
    paths = _create_story_files(output_dir)

    result = compact_archive(output_dir, dry_run=False)

    assert result.files_removed == 0
    assert all(path.exists() for path in paths.values())


def test_compact_cli_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(["compact"])

    assert args.cmd == "compact"
    assert args.apply is False
    assert args.show_files is False
