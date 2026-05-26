import json
from pathlib import Path

from local_story_archive.archive.repository import ArchiveRepository
from local_story_archive.archive.store import atomic_write_text


def _db_story(story_dir_path: Path) -> tuple[dict, list[dict], dict[str, list[dict]]] | None:
    output_dir = story_dir_path.parents[2]
    archive_db = output_dir / "archive.sqlite"
    if not archive_db.exists():
        return None
    story_id = story_dir_path.name.split("_", 1)[0]
    repo = ArchiveRepository(output_dir).connect()
    try:
        story = repo.get_story(story_id)
        if story is None:
            return None
        parts = repo.list_parts(story_id)
        paragraphs = {p["part_id"]: repo.list_paragraphs(p["part_id"]) for p in parts}
        return story, parts, paragraphs
    finally:
        repo.close()


def render_txt(story_dir_path: Path) -> str:
    """Render a downloaded story directory into a single concatenated .txt file.

    Reads metadata.json + each chapter's .txt body file, joins them in ordinal
    order, writes to <story_dir>/output/<slug>.txt, and returns the full content.
    """
    db_story = _db_story(story_dir_path)
    if db_story is None:
        meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
        parts = sorted(meta["parts"], key=lambda x: x["ordinal"])
        paragraphs_by_part = {}
    else:
        meta, parts, paragraphs_by_part = db_story
    parts_dir = story_dir_path / "parts"
    chunks = [f"{meta['title']}\nby {meta['author_username']}\n\n"]
    for p in parts:
        ord_ = int(p["ordinal"])
        if db_story is None:
            prefix = f"{ord_:02d}_{p['part_id']}_"
            candidates = list(parts_dir.glob(f"{prefix}*.txt"))
            if not candidates:
                continue
            body = candidates[0].read_text(encoding="utf-8")
        else:
            body = p["body_text"]
            if not body:
                body = "\n\n".join(
                    paragraph["text"] for paragraph in paragraphs_by_part.get(p["part_id"], [])
                )
            if not body:
                continue
        chunks.append(f"\n\n========\n{p['title']}\n========\n\n{body}\n")
    full = "".join(chunks)

    # Output filename: derive from story directory name, dropping the leading
    # "<story_id>_" prefix.
    dirname = story_dir_path.name
    slug_part = dirname.split("_", 1)[1] if "_" in dirname else dirname
    out_path = story_dir_path / "output" / f"{slug_part}.txt"
    atomic_write_text(out_path, full)
    return full
