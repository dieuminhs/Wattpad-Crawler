import sqlite3
from dataclasses import dataclass, field
from pathlib import Path



@dataclass
class CompactResult:
    files_removed: int = 0
    bytes_removed: int = 0
    files_skipped: int = 0
    db_bytes_removed: int = 0
    planned_paths: list[Path] = field(default_factory=list)



def _story_id_from_dir(story_dir: Path) -> str | None:
    if "_" not in story_dir.name:
        return None
    story_id = story_dir.name.split("_", 1)[0]
    return story_id if story_id.isdigit() else None



def _db_parts(conn: sqlite3.Connection, story_id: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT part_id, ordinal, body_text, raw_html
        FROM parts
        WHERE story_id = ?
        """,
        (story_id,),
    ).fetchall()
    return {str(row["part_id"]): row for row in rows}



def _part_id_from_part_file(path: Path) -> str | None:
    pieces = path.stem.split("_", 2)
    if len(pieces) < 2:
        return None
    return pieces[1]



def _part_id_from_comment_file(path: Path) -> str | None:
    name = path.name
    if not (name.endswith("_comments-inline.json") or name.endswith("_comments-end.json")):
        return None
    pieces = name.split("_", 2)
    if len(pieces) < 2:
        return None
    return pieces[1]



def _is_redundant_part_file(path: Path, parts: dict[str, sqlite3.Row]) -> bool:
    part_id = _part_id_from_part_file(path)
    if part_id is None or part_id not in parts:
        return False
    row = parts[part_id]
    if path.suffix == ".html":
        return bool(row["raw_html"])
    if path.suffix == ".txt":
        return bool(row["body_text"])
    if path.suffix == ".json":
        return True
    return False



def _is_redundant_comment_file(path: Path, parts: dict[str, sqlite3.Row]) -> bool:
    part_id = _part_id_from_comment_file(path)
    return part_id is not None and part_id in parts



def _candidate_paths(story_dir: Path, parts: dict[str, sqlite3.Row]) -> list[Path]:
    candidates: list[Path] = []
    output_dir = story_dir / "output"
    if output_dir.exists():
        candidates.extend(path for path in output_dir.iterdir() if path.is_file())

    parts_dir = story_dir / "parts"
    if not parts_dir.exists():
        return candidates

    for path in parts_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix in {".html", ".txt", ".json"} and _is_redundant_part_file(path, parts):
            candidates.append(path)
        elif _is_redundant_comment_file(path, parts):
            candidates.append(path)
    return candidates


def _compact_database(conn: sqlite3.Connection, *, dry_run: bool) -> int:
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(LENGTH(body_text) + LENGTH(raw_html)), 0) AS bytes_removed
            FROM parts
            WHERE (body_text != '' OR raw_html != '')
              AND EXISTS (
                  SELECT 1
                  FROM paragraphs
                  WHERE paragraphs.part_id = parts.part_id
              )
            """
        ).fetchone()
        bytes_removed = int(row["bytes_removed"] or 0)
        row = conn.execute(
            """
            SELECT COALESCE(SUM(LENGTH(text)), 0) AS bytes_removed
            FROM paragraphs
            WHERE text != '' AND html != ''
            """
        ).fetchone()
        bytes_removed += int(row["bytes_removed"] or 0)
    except sqlite3.OperationalError:
        return 0
    if not dry_run and bytes_removed:
        conn.execute(
            """
            UPDATE parts
            SET body_text = '', raw_html = ''
            WHERE (body_text != '' OR raw_html != '')
              AND EXISTS (
                  SELECT 1
                  FROM paragraphs
                  WHERE paragraphs.part_id = parts.part_id
              )
            """
        )
        conn.execute(
            """
            UPDATE paragraphs
            SET text = ''
            WHERE text != '' AND html != ''
            """
        )
        conn.commit()
        conn.execute("VACUUM")
    return bytes_removed



def compact_archive(output_dir: Path, *, dry_run: bool = True) -> CompactResult:
    """Remove regenerable archive files when archive.sqlite has canonical data."""
    db_path = output_dir / "archive.sqlite"
    stories_dir = output_dir / "stories"
    result = CompactResult()
    if not db_path.exists() or not stories_dir.exists():
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for story_dir in stories_dir.glob("*/*"):
            if not story_dir.is_dir():
                continue
            story_id = _story_id_from_dir(story_dir)
            if story_id is None:
                result.files_skipped += 1
                continue
            parts = _db_parts(conn, story_id)
            if not parts:
                result.files_skipped += 1
                continue
            for path in _candidate_paths(story_dir, parts):
                size = path.stat().st_size
                result.files_removed += 1
                result.bytes_removed += size
                result.planned_paths.append(path)
                if not dry_run:
                    path.unlink()
        result.db_bytes_removed = _compact_database(conn, dry_run=dry_run)
        result.bytes_removed += result.db_bytes_removed
        if not dry_run:
            conn.execute("PRAGMA optimize")
    finally:
        conn.close()
    return result
