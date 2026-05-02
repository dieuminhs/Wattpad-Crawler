from pathlib import Path

from wattpad_crawler.scrape.chapter_html import extract_chapter


def test_extract_chapter_body_text(fixtures_dir: Path):
    html = (fixtures_dir / "html_chapters/chapter_with_images.html").read_text()
    result = extract_chapter(html)
    assert "First paragraph of bold text." in result.text
    assert "Second italic paragraph." in result.text
    assert "Last paragraph." in result.text
    assert "nav junk" not in result.text
    assert "more junk" not in result.text


def test_extract_chapter_paragraph_count(fixtures_dir: Path):
    html = (fixtures_dir / "html_chapters/chapter_with_images.html").read_text()
    result = extract_chapter(html)
    assert len(result.paragraphs) == 4
    assert result.paragraphs[0]["id"] == "p1"


def test_extract_chapter_images(fixtures_dir: Path):
    html = (fixtures_dir / "html_chapters/chapter_with_images.html").read_text()
    result = extract_chapter(html)
    assert result.images == ["https://img.wattpad.com/inline/abc.jpg"]


def test_extract_chapter_handles_missing_container():
    """Falls back to body if .page-container isn't present."""
    html = "<html><body><pre data-p-id='p1'>just body</pre></body></html>"
    result = extract_chapter(html)
    assert "just body" in result.text


def test_extract_chapter_skips_empty_paragraphs():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">hi</pre>'
        '<pre data-p-id="p2"></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    # Empty paragraphs are kept in `paragraphs` list (preserves structure)
    # but excluded from `text` (no extra newlines).
    assert len(result.paragraphs) == 2
    assert result.text == "hi"


def test_extract_chapter_handles_p_tags_not_just_pre():
    """Wattpad has been observed using <p data-p-id> in some chapter layouts."""
    html = """
    <html><body><div class="page-container">
      <p data-p-id="p1">First.</p>
      <p data-p-id="p2">Second.</p>
    </div></body></html>
    """
    result = extract_chapter(html)
    assert "First." in result.text
    assert "Second." in result.text
    assert len(result.paragraphs) == 2


def test_extract_chapter_logs_warning_when_no_paragraphs(caplog):
    import logging
    html = "<html><body><div class='page-container'><span>no data-p-id</span></div></body></html>"
    with caplog.at_level(logging.WARNING, logger="wattpad_crawler.scrape.chapter_html"):
        result = extract_chapter(html)
    assert result.text == ""
    assert any("data-p-id" in rec.message for rec in caplog.records)
