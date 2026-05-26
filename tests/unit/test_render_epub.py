import json
import zipfile
from pathlib import Path

from local_story_archive.render.epub import render_epub


def test_render_epub_creates_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.html").write_text("<pre>Chapter one body.</pre>")
    (sd / "parts" / "02_101_two.html").write_text("<pre>Chapter two body.</pre>")
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "story_id": "42",
        "tags": ["x"], "description": "d",
        "parts": [
            {"part_id": "100", "ordinal": 1, "title": "Chapter One"},
            {"part_id": "101", "ordinal": 2, "title": "Chapter Two"},
        ],
    }))
    out_path = render_epub(sd)
    assert out_path.exists()
    assert out_path.suffix == ".epub"
    assert out_path.stat().st_size > 0


def test_render_epub_includes_cover_when_present(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "story_id": "42",
        "tags": [], "description": "",
        "parts": [],
    }))
    out_path = render_epub(sd)
    assert out_path.exists()


def test_render_epub_handles_missing_cover(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "story_id": "42",
        "tags": [], "description": "",
        "parts": [],
    }))
    # Should not raise even with no cover
    out_path = render_epub(sd)
    assert out_path.exists()

def test_render_epub_includes_export_preset_css(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "story_id": "42",
        "tags": [], "description": "", "parts": [],
    }))

    out_path = render_epub(sd, export_preset="compact")

    with zipfile.ZipFile(out_path) as archive:
        css = archive.read("EPUB/style/export.css").decode("utf-8")
    assert "--export-preset:compact" in css
    assert "line-height:1.45" in css
