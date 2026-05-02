import os
import re
from pathlib import Path

from wattpad_crawler.models import Story

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 80


def slugify(s: str) -> str:
    s = s.lower()
    s = _SLUG_RE.sub("-", s)
    s = s.strip("-")
    if len(s) > _SLUG_MAX:
        s = s[:_SLUG_MAX].rstrip("-")
    return s


def atomic_write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def story_dir(output_dir: Path, story: Story) -> Path:
    slug = slugify(story.title)
    return output_dir / "stories" / story.author_username / f"{story.story_id}_{slug}"
