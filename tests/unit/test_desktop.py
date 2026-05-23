from pathlib import Path

from wattpad_crawler import desktop


def test_default_app_data_dir_uses_localappdata_on_windows(monkeypatch):
    monkeypatch.setattr(desktop.os, "name", "nt", raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Tester\AppData\Local")
    monkeypatch.delenv("APPDATA", raising=False)

    assert desktop.default_app_data_dir() == Path(r"C:\Users\Tester\AppData\Local") / "Wattpad Crawler"
    assert desktop.default_archive_dir() == (
        Path(r"C:\Users\Tester\AppData\Local") / "Wattpad Crawler" / "wattpad-archive"
    )


def test_load_desktop_archive_dir_reads_saved_pointer(tmp_path, monkeypatch):
    app_data = tmp_path / "app-data"
    archive_dir = tmp_path / "existing archive"
    settings_path = app_data / desktop.DESKTOP_SETTINGS_FILE
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(f'archive_dir = "{archive_dir.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setattr(desktop, "default_app_data_dir", lambda: app_data)

    assert desktop.load_desktop_archive_dir() == archive_dir


def test_load_desktop_archive_dir_falls_back_on_invalid_settings(tmp_path, monkeypatch):
    app_data = tmp_path / "app-data"
    default_archive = app_data / "wattpad-archive"
    settings_path = app_data / desktop.DESKTOP_SETTINGS_FILE
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("archive_dir = [not valid toml\n", encoding="utf-8")
    monkeypatch.setattr(desktop, "default_app_data_dir", lambda: app_data)

    assert desktop.load_desktop_archive_dir() == default_archive


def test_write_startup_url_creates_parent_and_writes_url(tmp_path):
    startup_url_file = tmp_path / "nested" / "backend.url"

    desktop._write_startup_url(startup_url_file, "http://127.0.0.1:12345")

    assert startup_url_file.read_text(encoding="utf-8") == "http://127.0.0.1:12345"


def test_setup_logging_creates_rotating_log_file(tmp_path, monkeypatch):
    app_data = tmp_path / "app-data"
    monkeypatch.setattr(desktop, "default_app_data_dir", lambda: app_data)

    desktop._setup_logging(verbose=True)

    assert desktop.desktop_log_path() == app_data / "logs" / "backend.log"
    assert desktop.desktop_log_path().exists()

