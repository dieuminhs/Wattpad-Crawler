import sqlite3
from pathlib import Path
from typing import Self

from wattpad_crawler.models import PartStatus, Story, StoryStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    story_id            TEXT PRIMARY KEY,
    author_username     TEXT NOT NULL,
    title               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    last_remote_update  TEXT,
    last_local_update   TEXT,
    parts_count_remote  INTEGER DEFAULT 0,
    parts_count_local   INTEGER DEFAULT 0,
    metadata_json       TEXT
);

CREATE TABLE IF NOT EXISTS parts (
    story_id            TEXT NOT NULL,
    part_id             TEXT NOT NULL,
    ordinal             INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    body_hash           TEXT,
    comments_inline_done INTEGER DEFAULT 0,
    comments_end_done   INTEGER DEFAULT 0,
    last_error          TEXT,
    PRIMARY KEY (story_id, part_id),
    FOREIGN KEY (story_id) REFERENCES stories(story_id)
);

CREATE INDEX IF NOT EXISTS idx_parts_status ON parts(status);

CREATE TABLE IF NOT EXISTS runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    summary_json        TEXT
);
"""


class Manifest:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.path = output_dir / "_state.sqlite"
        self.conn: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        """Connection accessor that raises if not connected. Use this in CRUD methods."""
        if self.conn is None:
            raise RuntimeError("Manifest not connected; call connect() first")
        return self.conn

    def connect(self) -> Self:
        self.conn = sqlite3.connect(self.path)
        # Enable FK enforcement (off by default in SQLite).
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL allows concurrent readers + one writer; avoids "database is locked"
        # once the web UI in Plan 2 reads while the crawler writes.
        self.conn.execute("PRAGMA journal_mode = WAL")
        # Set row_factory once so every fetch returns sqlite3.Row.
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> Self:
        if self.conn is None:
            self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- story / part CRUD ---

    def upsert_story(self, story: Story) -> None:
        self.db.execute(
            """
            INSERT INTO stories(story_id, author_username, title, parts_count_remote)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(story_id) DO UPDATE SET
                author_username = excluded.author_username,
                title = excluded.title,
                parts_count_remote = excluded.parts_count_remote
            """,
            (story.story_id, story.author_username, story.title, len(story.parts)),
        )
        self.db.commit()

    def upsert_parts(self, story: Story) -> None:
        self.db.executemany(
            """
            INSERT INTO parts(story_id, part_id, ordinal)
            VALUES (?, ?, ?)
            ON CONFLICT(story_id, part_id) DO UPDATE SET ordinal = excluded.ordinal
            """,
            [(story.story_id, p.part_id, p.ordinal) for p in story.parts],
        )
        self.db.commit()

    def set_part_status(
        self,
        story_id: str,
        part_id: str,
        status: PartStatus,
        *,
        body_hash: str | None = None,
        last_error: str | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE parts SET status = ?,
                             body_hash = COALESCE(?, body_hash),
                             last_error = ?
            WHERE story_id = ? AND part_id = ?
            """,
            (status, body_hash, last_error, story_id, part_id),
        )
        self.db.commit()

    def set_story_status(self, story_id: str, status: StoryStatus) -> None:
        self.db.execute(
            "UPDATE stories SET status = ? WHERE story_id = ?",
            (status, story_id),
        )
        self.db.commit()

    def get_story(self, story_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM stories WHERE story_id = ?", (story_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_part(self, story_id: str, part_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM parts WHERE story_id = ? AND part_id = ?",
            (story_id, part_id),
        ).fetchone()
        return dict(row) if row else None

    def pending_parts_for(self, story_id: str) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT * FROM parts
            WHERE story_id = ? AND status NOT IN ('done', 'gone', 'private')
            ORDER BY ordinal
            """,
            (story_id,),
        ).fetchall()
        return [dict(r) for r in rows]
