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


# --- Phase 1 SAN-01 sanitization tests ---


def test_extract_chapter_strips_script_in_paragraph():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">hi <script>alert(1)</script>safe</pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert len(result.paragraphs) == 1
    h = result.paragraphs[0]["html"]
    assert "<script" not in h.lower()
    assert "alert" not in h
    assert "safe" in h


def test_extract_chapter_strips_onerror_handler():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><img src="x.jpg" onerror="alert(1)"></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "onerror" not in h.lower()
    assert "<img" in h.lower()
    assert 'src="x.jpg"' in h


def test_extract_chapter_preserves_data_p_id_on_inner_elements():
    # D-02: data-p-id is allowed on every *allowed* tag via attributes={"*": {"data-p-id"}}.
    # The plan originally used <span> here, but <span> is not in the D-01 tag allowlist —
    # nh3 strips disallowed tags entirely, dropping their attributes along with them. To
    # observe the universal data-p-id rule, the inner element must itself be allowed.
    # Using <b> (D-01 reading-rich) demonstrates the preservation cleanly.
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><b data-p-id="inner">child</b></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    # Outer paragraph's id field already captures p1; the html field contains
    # the inner <b> which should still have data-p-id (D-02 universal allowlist).
    assert 'data-p-id="inner"' in result.paragraphs[0]["html"]


def test_extract_chapter_strips_javascript_href_keeps_link_text():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><a href="javascript:alert(1)">click</a></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    # nh3 strips javascript: scheme but may keep the <a> with rel-injection
    # (RESEARCH Pitfall 1). We only require: no javascript: URL survives,
    # and the link text is preserved.
    assert "javascript:" not in h.lower()
    assert "click" in h


def test_extract_chapter_preserves_https_href():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><a href="https://example.com">x</a></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert 'href="https://example.com"' in result.paragraphs[0]["html"]


def test_extract_chapter_preserves_http_href():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><a href="http://example.com">x</a></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert 'href="http://example.com"' in result.paragraphs[0]["html"]


def test_extract_chapter_preserves_reading_rich_tags():
    """D-01: reading-rich allowlist preserves <b>, <i>, <em>, <strong>, <u>."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">a <b>bold</b> and <em>emph</em> '
        '<i>i</i> <strong>s</strong> <u>u</u> word</pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "<b>" in h and "</b>" in h
    assert "<em>" in h and "</em>" in h
    assert "<i>" in h and "</i>" in h
    assert "<strong>" in h and "</strong>" in h
    assert "<u>" in h and "</u>" in h


def test_extract_chapter_preserves_br_and_img_src_alt():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">line<br><img src="a.jpg" alt="cap"></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "<br" in h.lower()
    assert 'src="a.jpg"' in h
    assert 'alt="cap"' in h


def test_extract_chapter_strips_class_and_style_attributes():
    """D-03: class and style stripped from every tag."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">'
        '<img src="a.jpg" class="hero" style="width:100%">'
        '<a href="https://x" class="link" style="color:red">y</a>'
        '</pre></div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "class=" not in h
    assert "style=" not in h
    # core attrs survive
    assert 'src="a.jpg"' in h
    assert 'href="https://x"' in h


def test_extract_chapter_strips_disallowed_tag_keeps_text():
    """Tags not in the allowlist (e.g., <div>, <span> without data-p-id) are stripped."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">a <div>nested</div> b</pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "<div" not in h.lower()
    assert "nested" in h
    assert "a " in h and " b" in h


def test_extract_chapter_strips_data_attributes_other_than_p_id():
    """D-02: only data-p-id is allowed; other data-* attributes are stripped."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><img src="a.jpg" data-tracking="t1" data-p-id="inner"></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert 'data-tracking' not in h
    assert 'data-p-id="inner"' in h


def test_extract_chapter_html_field_present_for_every_paragraph():
    """Smoke test: sanitization does not remove the html key from the dict."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">one</pre>'
        '<pre data-p-id="p2">two</pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert len(result.paragraphs) == 2
    for p in result.paragraphs:
        assert "html" in p
        assert isinstance(p["html"], str)
