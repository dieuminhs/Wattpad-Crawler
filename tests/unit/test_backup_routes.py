import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from local_story_archive.archive.backup import create_backup
from local_story_archive.config import Config
from local_story_archive.web.app import build_app


def test_config_page_renders_backup_restore_controls(output_dir: Path):
    app = build_app(Config(output_dir=output_dir))
    client = TestClient(app)

    response = client.get("/config")

    assert response.status_code == 200
    assert "Backup &amp; restore" in response.text
    assert "/config/backup" in response.text
    assert "/config/restore" in response.text
    assert "Backups exclude" in response.text


def test_config_backup_returns_zip_download(output_dir: Path):
    story_dir = output_dir / "stories" / "alice" / "42_story"
    story_dir.mkdir(parents=True)
    (story_dir / "metadata.json").write_text("{}", encoding="utf-8")
    app = build_app(Config(output_dir=output_dir))
    client = TestClient(app)

    response = client.post("/config/backup")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "local-story-archive-backup" in response.headers["content-disposition"]
    backup_path = output_dir.parent / "download.zip"
    backup_path.write_bytes(response.content)
    with zipfile.ZipFile(backup_path) as archive:
        assert "backup-manifest.json" in archive.namelist()
        assert "stories/alice/42_story/metadata.json" in archive.namelist()


def test_config_restore_accepts_valid_backup(output_dir: Path, tmp_path: Path):
    source = tmp_path / "source"
    story_dir = source / "stories" / "alice" / "42_story"
    story_dir.mkdir(parents=True)
    (story_dir / "metadata.json").write_text(json.dumps({"story_id": "42"}), encoding="utf-8")
    backup_path = create_backup(source)
    app = build_app(Config(output_dir=output_dir))
    client = TestClient(app)

    with backup_path.open("rb") as backup_file:
        response = client.post(
            "/config/restore",
            files={"backup_file": (backup_path.name, backup_file, "application/zip")},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?restored=1"
    assert (output_dir / "stories" / "alice" / "42_story" / "metadata.json").exists()


def test_config_restore_invalid_upload_redirects_with_error(output_dir: Path, tmp_path: Path):
    bad_backup = tmp_path / "bad.zip"
    bad_backup.write_text("not a zip", encoding="utf-8")
    app = build_app(Config(output_dir=output_dir))
    client = TestClient(app)

    with bad_backup.open("rb") as backup_file:
        response = client.post(
            "/config/restore",
            files={"backup_file": (bad_backup.name, backup_file, "application/zip")},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/config?restore_error=")
