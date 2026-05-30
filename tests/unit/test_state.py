from pathlib import Path

import pytest

from local_story_archive.archive.state import Manifest
from local_story_archive.models import Part, Story


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


def make_story(story_id: str = "s1") -> Story:
    return Story(
        story_id=story_id,
        title="Test",
        author_username="alice",
        parts=[
            Part(part_id="p1", ordinal=1, title="One", url="https://w/p1"),
            Part(part_id="p2", ordinal=2, title="Two", url="https://w/p2"),
        ],
    )


def test_upsert_story_inserts(output_dir: Path):
    m = Manifest(output_dir).connect()
    m.upsert_story(make_story())
    row = m.get_story("s1")
    assert row is not None
    assert row["title"] == "Test"
    assert row["status"] == "pending"
    m.close()


def test_upsert_story_updates(output_dir: Path):
    m = Manifest(output_dir).connect()
    m.upsert_story(make_story())
    s = make_story()
    s.title = "Updated"
    m.upsert_story(s)
    assert m.get_story("s1")["title"] == "Updated"
    m.close()


def test_part_status_transitions(output_dir: Path):
    m = Manifest(output_dir).connect()
    s = make_story()
    m.upsert_story(s)
    m.upsert_parts(s)
    m.set_part_status("s1", "p1", "in_progress")
    m.set_part_status("s1", "p1", "done", body_hash="abc")
    p = m.get_part("s1", "p1")
    assert p["status"] == "done"
    assert p["body_hash"] == "abc"
    m.close()


def test_pending_parts_query(output_dir: Path):
    m = Manifest(output_dir).connect()
    s = make_story()
    m.upsert_story(s)
    m.upsert_parts(s)
    m.set_part_status("s1", "p1", "done")
    pending = m.pending_parts_for("s1")
    assert [p["part_id"] for p in pending] == ["p2"]
    m.close()


def test_set_part_status_with_error_message(output_dir: Path):
    m = Manifest(output_dir).connect()
    s = make_story()
    m.upsert_story(s)
    m.upsert_parts(s)
    m.set_part_status("s1", "p1", "failed", last_error="connection reset")
    p = m.get_part("s1", "p1")
    assert p["status"] == "failed"
    assert p["last_error"] == "connection reset"
    m.close()


def test_reset_story_marks_story_and_parts_pending(output_dir: Path):
    m = Manifest(output_dir).connect()
    s = make_story()
    m.upsert_story(s)
    m.upsert_parts(s)
    m.set_story_status("s1", "done")
    m.set_part_status("s1", "p1", "done", body_hash="abc")
    m.set_part_status("s1", "p2", "failed", last_error="connection reset")

    assert m.reset_story("s1") is True

    assert m.get_story("s1")["status"] == "pending"
    assert m.get_part("s1", "p1")["status"] == "pending"
    assert m.get_part("s1", "p1")["body_hash"] is None
    assert m.get_part("s1", "p2")["last_error"] is None
    assert [p["part_id"] for p in m.pending_parts_for("s1")] == ["p1", "p2"]
    m.close()


def test_reset_story_returns_false_for_missing_story(output_dir: Path):
    m = Manifest(output_dir).connect()
    assert m.reset_story("missing") is False
    m.close()


def test_get_missing_story_returns_none(output_dir: Path):
    m = Manifest(output_dir).connect()
    assert m.get_story("nonexistent") is None
    m.close()


def test_get_missing_part_returns_none(output_dir: Path):
    m = Manifest(output_dir).connect()
    assert m.get_part("s1", "p1") is None
    m.close()

def test_reset_failed_story_work_only_resets_failed_parts(output_dir: Path):
    m = Manifest(output_dir).connect()
    s = make_story()
    m.upsert_story(s)
    m.upsert_parts(s)
    m.set_part_status("s1", "p1", "done", body_hash="abc")
    m.set_part_status("s1", "p2", "failed", last_error="connection reset")

    assert m.reset_failed_story_work("s1") == 1

    assert m.get_part("s1", "p1")["status"] == "done"
    assert m.get_part("s1", "p1")["body_hash"] == "abc"
    assert m.get_part("s1", "p2")["status"] == "pending"
    assert m.get_part("s1", "p2")["last_error"] is None
    m.close()

def test_run_history_round_trips_summary(output_dir: Path):
    m = Manifest(output_dir).connect()
    run_id = m.start_run({"kind": "story", "status": "running"})
    m.finish_run(
        run_id,
        {"kind": "story", "status": "done", "stories": 1, "failures": 0},
    )

    runs = m.list_runs()

    assert runs[0]["run_id"] == run_id
    assert runs[0]["ended_at"]
    assert runs[0]["summary"]["kind"] == "story"
    assert runs[0]["summary"]["stories"] == 1
    m.close()
