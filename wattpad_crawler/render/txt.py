import json
from pathlib import Path

from wattpad_crawler.archive.store import atomic_write_text


def render_txt(story_dir_path: Path) -> str:
    """Render a downloaded story directory into a single concatenated .txt file.

    Reads metadata.json + each chapter's .txt body file, joins them in ordinal
    order, writes to <story_dir>/output/<slug>.txt, and returns the full content.
    """
    meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
    parts_dir = story_dir_path / "parts"
    chunks = [f"{meta['title']}\nby {meta['author_username']}\n\n"]
    for p in sorted(meta["parts"], key=lambda x: x["ordinal"]):
        ord_ = int(p["ordinal"])
        prefix = f"{ord_:02d}_{p['part_id']}_"
        candidates = list(parts_dir.glob(f"{prefix}*.txt"))
        if not candidates:
            continue
        body = candidates[0].read_text(encoding="utf-8")
        chunks.append(f"\n\n========\n{p['title']}\n========\n\n{body}\n")
    full = "".join(chunks)

    # Output filename: derive from story directory name, dropping the leading
    # "<story_id>_" prefix.
    dirname = story_dir_path.name
    slug_part = dirname.split("_", 1)[1] if "_" in dirname else dirname
    out_path = story_dir_path / "output" / f"{slug_part}.txt"
    atomic_write_text(out_path, full)
    return full
