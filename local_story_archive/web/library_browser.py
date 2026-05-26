import json
from pathlib import Path

from local_story_archive.archive.health import check_story_archive
from local_story_archive.archive.library import LibraryEntry
from local_story_archive.archive.repository import ArchiveRepository


def _with_health(entry: LibraryEntry, metadata: dict) -> LibraryEntry:
    health = check_story_archive(entry.storage_path, metadata)
    entry.health_status = health.status
    entry.health_summary = health.summary
    entry.health_issues = health.issues
    return entry


def scan_library(output_dir: Path) -> list[LibraryEntry]:
    """Walk <output>/stories/<author>/<id>_<slug>/ and return one LibraryEntry per
    story directory that contains a metadata.json. Sorted by (author, title)."""
    archive_db = output_dir / "archive.sqlite"
    if archive_db.exists():
        repo = ArchiveRepository(output_dir).connect()
        try:
            entries = repo.list_library_entries()
        finally:
            repo.close()
        if entries:
            return entries

    stories_root = output_dir / "stories"
    if not stories_root.exists():
        return []

    entries: list[LibraryEntry] = []
    for author_dir in stories_root.iterdir():
        if not author_dir.is_dir():
            continue
        for story_dir in author_dir.iterdir():
            if not story_dir.is_dir():
                continue
            meta_path = story_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            ordinals = [
                int(part.get("ordinal", 0))
                for part in meta.get("parts", []) or []
                if isinstance(part, dict) and part.get("ordinal") is not None
            ]
            entries.append(
                _with_health(
                    LibraryEntry(
                        story_id=str(meta.get("story_id", "")),
                        title=meta.get("title", ""),
                        author=meta.get("author_username", author_dir.name),
                        description=meta.get("description", ""),
                        tags=list(meta.get("tags", []) or []),
                        parts_count=len(meta.get("parts", []) or []),
                        dir_name=story_dir.name,
                        has_cover=(story_dir / "cover.jpg").exists(),
                        storage_path=story_dir,
                        first_ordinal=min(ordinals) if ordinals else None,
                        last_ordinal=max(ordinals) if ordinals else None,
                    ),
                    meta,
                )
            )
    entries.sort(key=lambda e: (e.author.lower(), e.title.lower()))
    return entries
