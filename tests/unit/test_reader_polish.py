from pathlib import Path

from fastapi.testclient import TestClient

from wattpad_crawler.config import Config
from wattpad_crawler.web.app import build_app


def _template(name: str) -> str:
    return (Path(__file__).parents[2] / "wattpad_crawler" / "web" / "templates" / name).read_text(
        encoding="utf-8"
    )


def test_reader_toolbar_renders_theme_font_and_spacing_controls():
    template = _template("reader.html")

    assert 'class="reader-toolbar"' in template
    assert 'data-reader-theme="light"' in template
    assert 'data-reader-theme="sepia"' in template
    assert 'data-reader-theme="dark"' in template
    assert 'data-reader-font-scale="small"' in template
    assert 'data-reader-font-scale="large"' in template
    assert 'data-reader-line-height="compact"' in template
    assert 'data-reader-line-height="relaxed"' in template
    assert "Reset reading settings" in template


def test_reader_template_persists_preferences_and_progress():
    template = _template("reader.html")

    assert "readerTheme" in template
    assert "readerFontScale" in template
    assert "readerLineHeight" in template
    assert "lastReadByStory" in template
    assert "readerScrollByChapter" in template
    assert "window.setTimeout" in template
    assert "window.scrollTo" in template


def test_library_continue_still_uses_last_read_storage():
    template = _template("library.html")

    assert "lastReadByStory" in template
    assert "data-continue-story" in template


def test_reader_theme_css_classes_served(output_dir: Path):
    app = build_app(Config(output_dir=output_dir))
    client = TestClient(app)

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert '.reader[data-theme="sepia"]' in response.text
    assert '.reader[data-theme="dark"]' in response.text
    assert '.reader[data-font-scale="small"] .chapter-body' in response.text
    assert '.reader[data-line-height="relaxed"] .chapter-body' in response.text
    assert ".comment-drawer" in response.text
    assert ".reader-floating-nav" in response.text
