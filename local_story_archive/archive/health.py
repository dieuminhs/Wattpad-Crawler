from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_story_archive.archive.store import _safe_path_part, slugify


@dataclass(frozen=True)
class ArchiveHealth:
    status: str
    summary: str
    issues: list[str]


def _part_stem(part: dict[str, Any]) -> str:
    ordinal = int(part.get("ordinal") or 0)
    part_id = _safe_path_part(str(part.get("part_id") or ""))
    title = slugify(str(part.get("title") or ""))
    return f"{ordinal:02d}_{part_id}_{title}"


def _comments_stem(part: dict[str, Any]) -> str:
    ordinal = int(part.get("ordinal") or 0)
    part_id = _safe_path_part(str(part.get("part_id") or ""))
    return f"{ordinal:02d}_{part_id}"


def check_story_archive(
    story_dir: Path,
    metadata: dict[str, Any],
    *,
    require_part_files: bool = True,
) -> ArchiveHealth:
    """Check whether an archived story has its expected local files."""
    issues: list[str] = []
    warnings: list[str] = []
    parts = [part for part in metadata.get("parts", []) or [] if isinstance(part, dict)]

    if not parts:
        issues.append("metadata has no chapters")

    parts_dir = story_dir / "parts"
    check_part_files = require_part_files and parts_dir.exists()
    if parts and require_part_files and not check_part_files:
        issues.append("parts folder is missing")

    expected_suffixes = (".json", ".html", ".txt")
    for part in parts:
        if not check_part_files:
            continue
        title = str(part.get("title") or part.get("part_id") or "unknown chapter")
        stem = _part_stem(part)
        comment_stem = _comments_stem(part)
        missing = [
            suffix for suffix in expected_suffixes if not (parts_dir / f"{stem}{suffix}").exists()
        ]
        if missing:
            issues.append(
                f"chapter {part.get('ordinal', '?')} {title!r} missing {', '.join(missing)}"
            )
        for kind in ("inline", "end"):
            if not (parts_dir / f"{comment_stem}_comments-{kind}.json").exists():
                warnings.append(f"chapter {part.get('ordinal', '?')} comments-{kind} missing")

    if metadata.get("cover_url") and not (story_dir / "cover.jpg").exists():
        warnings.append("cover image missing")

    output_dir = story_dir / "output"
    if not output_dir.exists() or not any(output_dir.glob("*.epub")):
        warnings.append("EPUB output missing")

    if issues:
        return ArchiveHealth("broken", f"{len(issues)} repair needed", issues + warnings)
    if warnings:
        return ArchiveHealth("warning", f"{len(warnings)} warnings", warnings)
    return ArchiveHealth("ok", "Archive complete", [])
