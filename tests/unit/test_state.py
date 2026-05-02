from pathlib import Path

from wattpad_crawler.archive.state import Manifest


def test_manifest_creates_schema(output_dir: Path):
    m = Manifest(output_dir)
    m.connect()
    rows = m.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"stories", "parts", "runs"}.issubset(names)
    m.close()


def test_manifest_reopen_is_idempotent(output_dir: Path):
    Manifest(output_dir).connect().close()
    Manifest(output_dir).connect().close()  # should not raise


def test_manifest_db_lives_at_expected_path(output_dir: Path):
    m = Manifest(output_dir)
    m.connect()
    m.close()
    assert (output_dir / "_state.sqlite").exists()
