import html
import json
from pathlib import Path

from wattpad_crawler.archive.store import atomic_write_text

_HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:Georgia,serif;max-width:42em;margin:2em auto;padding:0 1em;line-height:1.6;}}
h1,h2{{font-family:system-ui,sans-serif;}}
hr{{border:none;border-top:1px solid #ccc;margin:3em 0;}}
.chapter{{margin-bottom:4em;}}
</style></head><body>"""


def render_html(story_dir_path: Path) -> str:
    """Render a downloaded story directory into a single standalone HTML file."""
    meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
    parts_dir = story_dir_path / "parts"
    body_chunks = [
        f"<h1>{html.escape(meta['title'])}</h1>",
        f"<p><em>by {html.escape(meta['author_username'])}</em></p>",
    ]
    for p in sorted(meta["parts"], key=lambda x: x["ordinal"]):
        prefix = f"{int(p['ordinal']):02d}_{p['part_id']}_"
        candidates = list(parts_dir.glob(f"{prefix}*.html"))
        if not candidates:
            continue
        chapter_html = candidates[0].read_text(encoding="utf-8")
        body_chunks.append('<hr><div class="chapter">')
        body_chunks.append(f"<h2>{html.escape(p['title'])}</h2>")
        body_chunks.append(chapter_html)
        body_chunks.append("</div>")
    full = (
        _HEAD.format(title=html.escape(meta["title"]))
        + "\n".join(body_chunks)
        + "</body></html>"
    )

    dirname = story_dir_path.name
    slug_part = dirname.split("_", 1)[1] if "_" in dirname else dirname
    out_path = story_dir_path / "output" / f"{slug_part}.html"
    atomic_write_text(out_path, full)
    return full
