import json
import os
import re
import threading
from pathlib import Path

from wattpad_crawler.models import Comment, Part, Story
from wattpad_crawler.scrape.chapter_html import ChapterContent

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 80
_PATH_PART_RE = re.compile(r"[^A-Za-z0-9_\-]")


def slugify(s: str) -> str:
    s = s.lower()
    s = _SLUG_RE.sub("-", s)
    s = s.strip("-")
    if len(s) > _SLUG_MAX:
        s = s[:_SLUG_MAX].rstrip("-")
    return s


def _safe_path_part(s: str) -> str:
    """Make an external string safe to use as a single path component.

    Strips path separators, parent-dir refs, and any characters that aren't
    alphanumeric / underscore / hyphen. Empty input returns 'unknown'.
    """
    if not s:
        return "unknown"
    cleaned = _PATH_PART_RE.sub("_", s)
    cleaned = cleaned.strip("._-") or "unknown"
    return cleaned[:80]


def _tmp_path(path: Path) -> Path:
    """Per-process, per-thread tmp filename — avoids collisions if two writers
    race on the same target path."""
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    return path.with_suffix(path.suffix + suffix)


def atomic_write_text(path: Path, data: str) -> None:
    """Atomically write text. Process-kill safe (an interrupt leaves either the
    old file or no change; never a half-written one). NOT power-loss durable —
    we don't fsync, so a hard power cut after this returns may still lose the
    most recent write. Acceptable for a personal archive tool."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """See atomic_write_text — same guarantees."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    tmp.write_bytes(data)
    os.replace(tmp, path)


def story_dir(output_dir: Path, story: Story) -> Path:
    """Compute the canonical local directory for a story.

    Both author_username and story_id are sanitized before use as path
    components — they come from external API data and must not be trusted
    to stay within the output directory.
    """
    author = _safe_path_part(story.author_username)
    sid = _safe_path_part(story.story_id)
    slug = slugify(story.title)
    return output_dir / "stories" / author / f"{sid}_{slug}"


def _part_basename(part: Part) -> str:
    return f"{part.ordinal:02d}_{part.part_id}_{slugify(part.title)}"


def _comments_basename(part: Part) -> str:
    return f"{part.ordinal:02d}_{part.part_id}"


def _comment_to_dict(c: Comment) -> dict:
    return {
        "comment_id": c.comment_id,
        "user": c.user,
        "body": c.body,
        "created_at": c.created_at,
        "paragraph_id": c.paragraph_id,
        "replies": [_comment_to_dict(r) for r in c.replies],
    }


def write_part_files(
    output_dir: Path,
    story: Story,
    part: Part,
    content: ChapterContent,
    raw_html: str,
    inline_comments: list[Comment],
    end_comments: list[Comment],
) -> None:
    parts_dir = story_dir(output_dir, story) / "parts"
    base = _part_basename(part)
    cbase = _comments_basename(part)
    atomic_write_text(parts_dir / f"{base}.json", json.dumps({
        "part_id": part.part_id,
        "ordinal": part.ordinal,
        "title": part.title,
        "url": part.url,
        "last_modified": part.last_modified,
        "paragraphs": content.paragraphs,
        "images": content.images,
    }, ensure_ascii=False, indent=2))
    atomic_write_text(parts_dir / f"{base}.html", raw_html)
    atomic_write_text(parts_dir / f"{base}.txt", content.text)
    atomic_write_text(
        parts_dir / f"{cbase}_comments-inline.json",
        json.dumps([_comment_to_dict(c) for c in inline_comments], ensure_ascii=False, indent=2),
    )
    atomic_write_text(
        parts_dir / f"{cbase}_comments-end.json",
        json.dumps([_comment_to_dict(c) for c in end_comments], ensure_ascii=False, indent=2),
    )
