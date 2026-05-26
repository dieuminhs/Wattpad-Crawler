import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_NAME = "Local Story Archive"
MANIFEST_NAME = "backup-manifest.json"
EXCLUDED_NAMES = {"_config.toml"}
EXCLUDED_SUFFIXES = {".tmp", ".wal", ".shm", "-wal", "-shm"}
EXCLUDED_DIRS = {".pytest_cache", ".ruff_cache", "__pycache__"}


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class RestoreSummary:
    files_restored: int


def _is_excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return True
    if path.name in EXCLUDED_NAMES:
        return True
    return any(path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _iter_backup_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and not _is_excluded(path, output_dir)
    )


def create_backup(output_dir: Path) -> Path:
    """Create a portable archive backup zip without exporting local credentials."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = Path(tempfile.gettempdir()) / f"local-story-archive-backup-{timestamp}.zip"
    files = _iter_backup_files(output_dir)
    manifest = {
        "app": APP_NAME,
        "created_at": datetime.now(UTC).isoformat(),
        "file_count": len(files),
        "excluded": ["_config.toml", "temporary files", "SQLite WAL/SHM files"],
        "note": "Sensitive local config, including the Wattpad cookie, is not included.",
    }
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        for path in files:
            archive.write(path, path.relative_to(output_dir).as_posix())
    return backup_path


def _load_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except KeyError as exc:
        raise BackupError("backup manifest is missing") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    if manifest.get("app") != APP_NAME:
        raise BackupError("backup was not created by Local Story Archive")
    return manifest


def _safe_restore_target(output_dir: Path, name: str) -> Path:
    if name == MANIFEST_NAME or name.endswith("/"):
        raise BackupError("internal archive entry cannot be restored")
    target = (output_dir / name).resolve()
    root = output_dir.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BackupError("backup contains an unsafe path") from exc
    return target


def restore_backup(output_dir: Path, backup_path: Path) -> RestoreSummary:
    """Safely merge a Local Story Archive backup zip into an archive directory."""
    if backup_path.suffix.lower() != ".zip":
        raise BackupError("backup must be a .zip file")
    try:
        archive = zipfile.ZipFile(backup_path)
    except zipfile.BadZipFile as exc:
        raise BackupError("backup zip is invalid") from exc
    restored = 0
    with archive:
        _load_manifest(archive)
        output_dir.mkdir(parents=True, exist_ok=True)
        for info in archive.infolist():
            if info.filename == MANIFEST_NAME or info.is_dir():
                continue
            target = _safe_restore_target(output_dir, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info.filename))
            restored += 1
    return RestoreSummary(files_restored=restored)
