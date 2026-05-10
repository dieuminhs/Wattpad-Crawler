import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

from wattpad_crawler.archive.library import LibraryEntry
from wattpad_crawler.archive.store import slugify, story_dir
from wattpad_crawler.models import Comment, Part, Story
from wattpad_crawler.scrape.chapter_html import ChapterContent

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
    story_id            TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    author_username     TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    cover_url           TEXT NOT NULL DEFAULT '',
    votes               INTEGER NOT NULL DEFAULT 0,
    reads               INTEGER NOT NULL DEFAULT 0,
    completed           INTEGER NOT NULL DEFAULT 0,
    last_modified       TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS story_tags (
    story_id            TEXT NOT NULL,
    tag                 TEXT NOT NULL,
    ordinal             INTEGER NOT NULL,
    PRIMARY KEY (story_id, tag),
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS parts (
    story_id            TEXT NOT NULL,
    part_id             TEXT PRIMARY KEY,
    ordinal             INTEGER NOT NULL,
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL DEFAULT '',
    last_modified       TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    body_text           TEXT NOT NULL DEFAULT '',
    raw_html            TEXT NOT NULL DEFAULT '',
    body_hash           TEXT,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS paragraphs (
    part_id             TEXT NOT NULL,
    paragraph_id        TEXT NOT NULL,
    ordinal             INTEGER NOT NULL,
    text                TEXT NOT NULL DEFAULT '',
    html                TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (part_id, paragraph_id),
    FOREIGN KEY (part_id) REFERENCES parts(part_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comments (
    comment_id          TEXT PRIMARY KEY,
    part_id             TEXT NOT NULL,
    parent_comment_id   TEXT,
    paragraph_id        TEXT,
    comment_kind        TEXT NOT NULL,
    depth               INTEGER NOT NULL DEFAULT 0,
    user                TEXT NOT NULL DEFAULT '',
    body                TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT '',
    ordinal             INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (part_id) REFERENCES parts(part_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_comment_id) REFERENCES comments(comment_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at            TEXT,
    summary_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS artifacts (
    story_id            TEXT NOT NULL,
    format              TEXT NOT NULL,
    path                TEXT NOT NULL,
    content_hash        TEXT,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (story_id, format),
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stories_author_title ON stories(author_username, title);
CREATE INDEX IF NOT EXISTS idx_parts_story_ordinal ON parts(story_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_comments_part_paragraph ON comments(part_id, paragraph_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_comment_id);
"""


class ArchiveRepository:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.path = output_dir / "archive.sqlite"
        self.conn: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("ArchiveRepository not connected; call connect() first")
        return self.conn

    def connect(self) -> Self:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(_SCHEMA)
        row = self.conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
        elif row["version"] != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported archive schema version: {row['version']}")
        self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> Self:
        if self.conn is None:
            self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def upsert_story(self, story: Story) -> None:
        self.db.execute(
            """
            INSERT INTO stories(
                story_id, title, author_username, description, cover_url,
                votes, reads, completed, last_modified, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(story_id) DO UPDATE SET
                title = excluded.title,
                author_username = excluded.author_username,
                description = excluded.description,
                cover_url = excluded.cover_url,
                votes = excluded.votes,
                reads = excluded.reads,
                completed = excluded.completed,
                last_modified = excluded.last_modified,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                story.story_id,
                story.title,
                story.author_username,
                story.description,
                story.cover_url,
                story.votes,
                story.reads,
                int(story.completed),
                story.last_modified,
            ),
        )
        self.db.execute("DELETE FROM story_tags WHERE story_id = ?", (story.story_id,))
        self.db.executemany(
            "INSERT INTO story_tags(story_id, tag, ordinal) VALUES (?, ?, ?)",
            [(story.story_id, tag, index) for index, tag in enumerate(story.tags)],
        )

        for part in story.parts:
            self.db.execute(
                """
                INSERT INTO parts(story_id, part_id, ordinal, title, url, last_modified)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(part_id) DO UPDATE SET
                    story_id = excluded.story_id,
                    ordinal = excluded.ordinal,
                    title = excluded.title,
                    url = excluded.url,
                    last_modified = excluded.last_modified,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    story.story_id,
                    part.part_id,
                    part.ordinal,
                    part.title,
                    part.url,
                    part.last_modified,
                ),
            )

    def upsert_part(
        self,
        story_id: str,
        part: Part,
        content: ChapterContent,
        raw_html: str,
        inline_comments: list[Comment],
        end_comments: list[Comment],
        *,
        body_hash: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO parts(
                story_id, part_id, ordinal, title, url, last_modified, status,
                body_text, raw_html, body_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'done', ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(part_id) DO UPDATE SET
                story_id = excluded.story_id,
                ordinal = excluded.ordinal,
                title = excluded.title,
                url = excluded.url,
                last_modified = excluded.last_modified,
                status = 'done',
                body_text = excluded.body_text,
                raw_html = excluded.raw_html,
                body_hash = excluded.body_hash,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                story_id,
                part.part_id,
                part.ordinal,
                part.title,
                part.url,
                part.last_modified,
                content.text,
                raw_html,
                body_hash,
            ),
        )
        self.db.execute("DELETE FROM paragraphs WHERE part_id = ?", (part.part_id,))
        self.db.executemany(
            """
            INSERT INTO paragraphs(part_id, paragraph_id, ordinal, text, html)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    part.part_id,
                    str(paragraph.get("id") or index),
                    index,
                    paragraph.get("text", ""),
                    paragraph.get("html", ""),
                )
                for index, paragraph in enumerate(content.paragraphs)
                if isinstance(paragraph, dict)
            ],
        )
        self.db.execute("DELETE FROM comments WHERE part_id = ?", (part.part_id,))
        self._insert_comments(part.part_id, inline_comments, "inline")
        self._insert_comments(part.part_id, end_comments, "end")

    def _insert_comments(
        self,
        part_id: str,
        comments: list[Comment],
        comment_kind: str,
        parent_comment_id: str | None = None,
        depth: int = 0,
    ) -> None:
        for ordinal, comment in enumerate(comments):
            self.db.execute(
                """
                INSERT INTO comments(
                    comment_id, part_id, parent_comment_id, paragraph_id,
                    comment_kind, depth, user, body, created_at, ordinal
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(comment_id) DO UPDATE SET
                    part_id = excluded.part_id,
                    parent_comment_id = excluded.parent_comment_id,
                    paragraph_id = excluded.paragraph_id,
                    comment_kind = excluded.comment_kind,
                    depth = excluded.depth,
                    user = excluded.user,
                    body = excluded.body,
                    created_at = excluded.created_at,
                    ordinal = excluded.ordinal
                """,
                (
                    comment.comment_id,
                    part_id,
                    parent_comment_id,
                    comment.paragraph_id,
                    comment_kind,
                    depth,
                    comment.user,
                    comment.body,
                    comment.created_at,
                    ordinal,
                ),
            )
            self._insert_comments(
                part_id,
                comment.replies,
                comment_kind,
                comment.comment_id,
                depth + 1,
            )

    def get_story(self, story_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
        if row is None:
            return None
        story = dict(row)
        story["completed"] = bool(story["completed"])
        story["tags"] = [
            tag_row["tag"]
            for tag_row in self.db.execute(
                "SELECT tag FROM story_tags WHERE story_id = ? ORDER BY ordinal",
                (story_id,),
            )
        ]
        return story

    def list_parts(self, story_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM parts WHERE story_id = ? ORDER BY ordinal",
                (story_id,),
            )
        ]

    def list_paragraphs(self, part_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM paragraphs WHERE part_id = ? ORDER BY ordinal",
                (part_id,),
            )
        ]

    def comments_by_paragraph(self, part_id: str) -> dict[str, list[dict[str, Any]]]:
        comments = self._comments_for(part_id, "inline")
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for comment in comments:
            paragraph_id = comment.get("paragraph_id")
            if paragraph_id:
                grouped[str(paragraph_id)].append(comment)
        return dict(grouped)

    def end_comments(self, part_id: str) -> list[dict[str, Any]]:
        return self._comments_for(part_id, "end")

    def _comments_for(self, part_id: str, comment_kind: str) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.db.execute(
                """
                SELECT * FROM comments
                WHERE part_id = ? AND comment_kind = ?
                ORDER BY depth, ordinal
                """,
                (part_id, comment_kind),
            )
        ]
        by_parent: defaultdict[str | None, list[dict[str, Any]]] = defaultdict(list)
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            comment = {
                "comment_id": row["comment_id"],
                "user": row["user"],
                "body": row["body"],
                "created_at": row["created_at"],
                "paragraph_id": row["paragraph_id"],
                "replies": [],
            }
            by_id[row["comment_id"]] = comment
            by_parent[row["parent_comment_id"]].append(comment)

        for row in rows:
            parent_id = row["parent_comment_id"]
            if parent_id and parent_id in by_id:
                by_id[parent_id]["replies"].append(by_id[row["comment_id"]])

        return by_parent[None]

    def list_library_entries(self) -> list[LibraryEntry]:
        entries = []
        rows = self.db.execute(
            """
            SELECT s.*, COUNT(p.part_id) AS parts_count
            FROM stories s
            LEFT JOIN parts p ON p.story_id = s.story_id
            GROUP BY s.story_id
            ORDER BY lower(s.author_username), lower(s.title)
            """
        )
        for row in rows:
            story = self.get_story(row["story_id"])
            if story is None:
                continue
            story_path = story_dir(
                self.output_dir,
                Story(
                    story_id=story["story_id"],
                    title=story["title"],
                    author_username=story["author_username"],
                ),
            )
            entries.append(
                LibraryEntry(
                    story_id=story["story_id"],
                    title=story["title"],
                    author=story["author_username"],
                    description=story["description"],
                    tags=story["tags"],
                    parts_count=row["parts_count"],
                    dir_name=f"{story['story_id']}_{slugify(story['title'])}",
                    has_cover=(story_path / "cover.jpg").exists(),
                    storage_path=story_path,
                )
            )
        return entries

    def record_artifact(
        self,
        story_id: str,
        fmt: str,
        path: Path,
        content_hash: str | None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO artifacts(story_id, format, path, content_hash, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(story_id, format) DO UPDATE SET
                path = excluded.path,
                content_hash = excluded.content_hash,
                updated_at = CURRENT_TIMESTAMP
            """,
            (story_id, fmt, str(path), content_hash),
        )
