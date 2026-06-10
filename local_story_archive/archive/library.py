from dataclasses import dataclass
from pathlib import Path



def has_cover_file(story_dir: Path) -> bool:
    """Return True when a story has a usable local cover image."""
    cover_path = story_dir / "cover.jpg"
    try:
        return cover_path.is_file() and cover_path.stat().st_size > 0
    except OSError:
        return False

@dataclass
class LibraryEntry:
    story_id: str
    title: str
    author: str
    description: str
    tags: list[str]
    parts_count: int
    dir_name: str
    has_cover: bool
    storage_path: Path
    bookmarked: bool = False
    first_ordinal: int | None = None
    last_ordinal: int | None = None
    health_status: str = "unknown"
    health_summary: str = "Not checked"
    health_issues: list[str] | None = None
    last_archived: str = ""
