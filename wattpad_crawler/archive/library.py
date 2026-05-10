from dataclasses import dataclass
from pathlib import Path


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
