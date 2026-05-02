import json
from pathlib import Path

from wattpad_crawler.render.txt import render_txt


def test_render_txt_concatenates_chapters_in_order(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi" / "parts"
    sd.mkdir(parents=True)
    (sd / "01_100_one.txt").write_text("Chapter one body.")
    (sd / "02_101_two.txt").write_text("Chapter two body.")
    (sd.parent / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [
            {"part_id": "100", "ordinal": 1, "title": "One"},
            {"part_id": "101", "ordinal": 2, "title": "Two"},
        ],
    }))
    out = render_txt(sd.parent)
    assert "Chapter one body." in out
    assert "Chapter two body." in out
    assert out.index("Chapter one body.") < out.index("Chapter two body.")
    assert "One" in out  # chapter title appears


def test_render_txt_handles_missing_part_files(output_dir: Path):
    """If a chapter .txt file is missing, render proceeds without crashing."""
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "Missing"}],
    }))
    # No .txt files exist — should still render header but no body
    out = render_txt(sd)
    assert "Hi" in out  # title is in output
    assert "Missing" not in out  # chapter not in output (no body file)


def test_render_txt_writes_output_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    parts = sd / "parts"
    parts.mkdir(parents=True)
    (parts / "01_100_one.txt").write_text("Body.")
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    render_txt(sd)
    assert (sd / "output" / "hi.txt").exists()
