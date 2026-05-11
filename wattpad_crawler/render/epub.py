import json
from pathlib import Path

from ebooklib import epub

from wattpad_crawler.archive.repository import ArchiveRepository


def _db_story(story_dir_path: Path) -> tuple[dict, list[dict]] | None:
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
        return story, repo.list_parts(story_id)
    finally:
        repo.close()


def render_epub(story_dir_path: Path) -> Path:
    """Render a downloaded story directory into an EPUB file using EbookLib."""
    db_story = _db_story(story_dir_path)
    if db_story is None:
        meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
        parts = sorted(meta["parts"], key=lambda x: x["ordinal"])
    else:
        meta, parts = db_story
    parts_dir = story_dir_path / "parts"

    book = epub.EpubBook()
    book.set_identifier(f"wattpad-{meta['story_id']}")
    book.set_title(meta["title"])
    book.set_language("en")
    book.add_author(meta["author_username"])
    if meta.get("description"):
        book.add_metadata("DC", "description", meta["description"])

    cover_path = story_dir_path / "cover.jpg"
    if cover_path.exists():
        book.set_cover("cover.jpg", cover_path.read_bytes())

    chapters = []
    for p in parts:
        if db_story is None:
            prefix = f"{int(p['ordinal']):02d}_{p['part_id']}_"
            candidates = list(parts_dir.glob(f"{prefix}*.html"))
            if not candidates:
                continue
            body = candidates[0].read_text(encoding="utf-8")
        else:
            body = p["raw_html"]
            if not body:
                continue
        ch = epub.EpubHtml(
            title=p["title"],
            file_name=f"chap_{int(p['ordinal']):02d}.xhtml",
            lang="en",
        )
        ch.content = f"<h1>{p['title']}</h1>\n{body}"
        book.add_item(ch)
        chapters.append(ch)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]

    out_dir = story_dir_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    dirname = story_dir_path.name
    slug_part = dirname.split("_", 1)[1] if "_" in dirname else dirname
    out_path = out_dir / f"{slug_part}.epub"
    epub.write_epub(str(out_path), book)
    return out_path
