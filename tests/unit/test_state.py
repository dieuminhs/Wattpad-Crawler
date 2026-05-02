from pathlib import Path

import pytest

from wattpad_crawler.archive.state import Manifest


def test_manifest_creates_schema(output_dir: Path):
    m = Manifest(output_dir).connect()
    rows = m.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"stories", "parts", "runs"}.issubset(names)
    m.close()


def test_manifest_reopen_is_idempotent(output_dir: Path):
    Manifest(output_dir).connect().close()
    Manifest(output_dir).connect().close()  # should not raise


def test_manifest_db_lives_at_expected_path(output_dir: Path):
    m = Manifest(output_dir).connect()
    m.close()
    assert (output_dir / "_state.sqlite").exists()


def test_manifest_enables_foreign_keys(output_dir: Path):
    m = Manifest(output_dir).connect()
    fk_state = m.db.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_state == 1, "foreign_keys pragma must be ON"
    m.close()


def test_manifest_uses_wal_journal(output_dir: Path):
    m = Manifest(output_dir).connect()
    journal = m.db.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal == "wal"
    m.close()


def test_manifest_row_factory_returns_rows(output_dir: Path):
    """Verify row_factory is set so fetchone returns sqlite3.Row (dict-like)."""
    m = Manifest(output_dir).connect()
    m.db.execute("INSERT INTO stories(story_id, author_username, title) VALUES (?, ?, ?)",
                 ("s1", "alice", "Test"))
    m.db.commit()
    row = m.db.execute("SELECT story_id, title FROM stories WHERE story_id = 's1'").fetchone()
    assert row["story_id"] == "s1"
    assert row["title"] == "Test"
    m.close()


def test_manifest_db_property_raises_before_connect(output_dir: Path):
    m = Manifest(output_dir)
    with pytest.raises(RuntimeError, match="not connected"):
        _ = m.db


def test_manifest_context_manager(output_dir: Path):
    with Manifest(output_dir) as m:
        assert m.conn is not None
    assert m.conn is None
