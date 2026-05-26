import json
import zipfile
from pathlib import Path

import pytest

from local_story_archive.archive.backup import BackupError, create_backup, restore_backup


def test_create_backup_includes_archive_files_and_excludes_cookie_config(output_dir: Path):
    story_dir = output_dir / "stories" / "alice" / "42_story"
    parts_dir = story_dir / "parts"
    output = story_dir / "output"
    parts_dir.mkdir(parents=True)
    output.mkdir()
    (story_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (parts_dir / "01_100_one.txt").write_text("body", encoding="utf-8")
    (output / "story.epub").write_bytes(b"epub")
    (output_dir / "_state.sqlite").write_bytes(b"state")
    (output_dir / "archive.sqlite").write_bytes(b"repo")
    (output_dir / "archive.sqlite-wal").write_bytes(b"wal")
    (output_dir / "_config.toml").write_text('cookie = "secret"', encoding="utf-8")

    backup_path = create_backup(output_dir)

    with zipfile.ZipFile(backup_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("backup-manifest.json"))
    assert manifest["app"] == "Local Story Archive"
    assert manifest["file_count"] == 5
    assert "stories/alice/42_story/metadata.json" in names
    assert "stories/alice/42_story/parts/01_100_one.txt" in names
    assert "stories/alice/42_story/output/story.epub" in names
    assert "_state.sqlite" in names
    assert "archive.sqlite" in names
    assert "_config.toml" not in names
    assert "archive.sqlite-wal" not in names


def test_restore_backup_merges_without_deleting_existing_files(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "stories" / "alice" / "42_story").mkdir(parents=True)
    (source / "stories" / "alice" / "42_story" / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    backup_path = create_backup(source)

    summary = restore_backup(target, backup_path)

    assert summary.files_restored == 1
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (target / "stories" / "alice" / "42_story" / "metadata.json").exists()


def test_restore_backup_rejects_missing_manifest(tmp_path: Path):
    backup_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(backup_path, "w") as archive:
        archive.writestr("stories/alice/42_story/metadata.json", "{}")

    with pytest.raises(BackupError, match="manifest"):
        restore_backup(tmp_path / "target", backup_path)


def test_restore_backup_rejects_path_traversal(tmp_path: Path):
    backup_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(backup_path, "w") as archive:
        archive.writestr("backup-manifest.json", json.dumps({"app": "Local Story Archive"}))
        archive.writestr("../evil.txt", "nope")

    with pytest.raises(BackupError, match="unsafe path"):
        restore_backup(tmp_path / "target", backup_path)
