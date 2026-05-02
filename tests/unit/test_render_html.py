import json
from pathlib import Path

from wattpad_crawler.render.html import render_html


def test_render_html_single_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.html").write_text(
        '<pre data-p-id="x">First.</pre><pre data-p-id="y">Second.</pre>'
    )
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "Chapter One"}],
    }))
    out = render_html(sd)
    assert "<title>Hi</title>" in out
    assert "Chapter One" in out
    assert "First." in out
    assert "Second." in out


def test_render_html_escapes_title(output_dir: Path):
    """Story title with HTML special chars must be escaped in <title>."""
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "<script>alert(1)</script>",
        "author_username": "bob",
        "parts": [],
    }))
    out = render_html(sd)
    assert "<title>&lt;script&gt;alert(1)&lt;/script&gt;</title>" in out
    assert "<script>alert(1)</script>" not in out  # never raw


def test_render_html_writes_output_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "parts": [],
    }))
    render_html(sd)
    assert (sd / "output" / "hi.html").exists()
