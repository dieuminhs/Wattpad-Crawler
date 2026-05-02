import sqlite3
from pathlib import Path
from typing import Self

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

    def connect(self) -> Self:
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
