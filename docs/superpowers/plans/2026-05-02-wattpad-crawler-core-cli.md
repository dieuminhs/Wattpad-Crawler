# Local Story Archive — Core + CLI Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working CLI that archives a Wattpad user's library, reading lists, favorites, or individual stories — chapters + inline images + all comments + EPUB/TXT/HTML rendering — to a local append-only archive.

**Architecture:** Hybrid scraper. Wattpad's JSON API drives discovery, metadata, and comments. Story chapter bodies are fetched as HTML and parsed with BeautifulSoup to preserve formatting and images. SQLite manifest tracks per-part state so runs are resumable. All file writes are atomic. Single Python package, layered: `client → api/+scrape/ → archive/ → render/ → jobs → cli`.

**Tech Stack:** Python 3.11+, httpx, beautifulsoup4 + lxml, ebooklib, pytest + vcrpy, sqlite3 (stdlib), tomllib (stdlib), argparse (stdlib).

**Scope of this plan:** Everything except the web UI. The web UI is Plan 2.

---

## File Structure

```
local_story_archive/
├── __init__.py                     # version
├── cli.py                          # argparse entry; calls jobs.py
├── config.py                       # loads <output-dir>/_config.toml
├── client.py                       # httpx session: cookie auth, rate-limit, retry, UA
├── jobs.py                         # orchestrator used by CLI (and later Web UI)
├── models.py                       # dataclasses: Story, Part, Comment, etc.
├── api/
│   ├── __init__.py
│   ├── user.py                     # library, reading_lists, favorites
│   ├── story.py                    # story metadata + parts list
│   └── comments.py                 # paginated inline + end-of-part
├── scrape/
│   ├── __init__.py
│   └── chapter_html.py             # extract body text + image refs from HTML
├── archive/
│   ├── __init__.py
│   ├── state.py                    # SQLite manifest
│   └── store.py                    # atomic filesystem writes
└── render/
    ├── __init__.py
    ├── txt.py
    ├── html.py
    └── epub.py

tests/
├── conftest.py                     # shared fixtures (tmp output dir, fake responses)
├── unit/
│   ├── test_config.py
│   ├── test_client.py
│   ├── test_state.py
│   ├── test_store.py
│   ├── test_chapter_html.py
│   ├── test_api_user.py
│   ├── test_api_story.py
│   ├── test_api_comments.py
│   ├── test_render_txt.py
│   ├── test_render_html.py
│   ├── test_render_epub.py
│   ├── test_jobs.py
│   └── test_cli.py
├── integration/
│   ├── cassettes/                  # vcrpy YAML
│   └── test_end_to_end.py
└── fixtures/
    ├── html_chapters/
    └── api_responses/

pyproject.toml
.gitignore
README.md
```

---

## Phase 0 — Bootstrap

### Task 1: Project skeleton & dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `local_story_archive/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "local-story-archive"
version = "0.1.0"
description = "Archive Wattpad stories locally before they're removed"
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "ebooklib>=0.18",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-vcr>=1.0.2",
  "vcrpy>=6.0",
  "ruff>=0.5",
]

[project.scripts]
local-story-archive = "local_story_archive.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: hits live Wattpad API; skipped by default"]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
*.egg-info/
dist/
build/
wattpad-archive/
.vcr_*
```

- [ ] **Step 3: Create `local_story_archive/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Create `tests/__init__.py`** (empty file)

- [ ] **Step 5: Create `tests/conftest.py`**

```python
from pathlib import Path
import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "wattpad-archive"
    out.mkdir()
    return out
```

- [ ] **Step 6: Install and verify**

```bash
python -m venv .venv
.venv/Scripts/activate    # Windows; on Linux/Mac use source .venv/bin/activate
pip install -e ".[dev]"
pytest --collect-only
```

Expected: `collected 0 items` (no tests yet, but no import errors).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore local_story_archive/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: project skeleton + deps"
```

---

## Phase 1 — Config + HTTP Client

### Task 2: Config loader

**Files:**
- Create: `local_story_archive/config.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/__init__.py` (empty)

- [ ] **Step 1: Write the failing test** — `tests/unit/test_config.py`

```python
from pathlib import Path
import pytest
from local_story_archive.config import Config, load_config, ConfigError


def test_load_config_creates_default_when_missing(output_dir: Path):
    cfg = load_config(output_dir)
    assert isinstance(cfg, Config)
    assert cfg.output_dir == output_dir
    assert cfg.cookie == ""
    assert cfg.rate_limit_per_sec == 2.0
    assert cfg.workers_per_story == 3
    assert (output_dir / "_config.toml").exists()


def test_load_config_reads_existing(output_dir: Path):
    (output_dir / "_config.toml").write_text(
        'cookie = "abc123"\n'
        "rate_limit_per_sec = 0.5\n"
        "workers_per_story = 5\n"
    )
    cfg = load_config(output_dir)
    assert cfg.cookie == "abc123"
    assert cfg.rate_limit_per_sec == 0.5
    assert cfg.workers_per_story == 5


def test_load_config_rejects_bad_toml(output_dir: Path):
    (output_dir / "_config.toml").write_text("not a [valid toml")
    with pytest.raises(ConfigError):
        load_config(output_dir)
```

- [ ] **Step 2: Run test — should fail**

```bash
pytest tests/unit/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'local_story_archive.config'`.

- [ ] **Step 3: Implement `local_story_archive/config.py`**

```python
from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigError(Exception):
    pass


@dataclass
class Config:
    output_dir: Path
    cookie: str = ""
    rate_limit_per_sec: float = 2.0
    workers_per_story: int = 3
    user_agent: str = "local-story-archive/0.1 (+local archive tool)"


_DEFAULT_TOML = (
    '# Paste your Wattpad session cookie here (the value of the "token" cookie)\n'
    'cookie = ""\n'
    "rate_limit_per_sec = 2.0\n"
    "workers_per_story = 3\n"
)


def load_config(output_dir: Path) -> Config:
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "_config.toml"
    if not config_path.exists():
        config_path.write_text(_DEFAULT_TOML, encoding="utf-8")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {config_path}: {e}") from e
    return Config(
        output_dir=output_dir,
        cookie=data.get("cookie", ""),
        rate_limit_per_sec=float(data.get("rate_limit_per_sec", 2.0)),
        workers_per_story=int(data.get("workers_per_story", 3)),
    )
```

- [ ] **Step 4: Run tests — should pass**

```bash
pytest tests/unit/test_config.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/config.py tests/unit/test_config.py tests/unit/__init__.py
git commit -m "feat(config): TOML config loader with sensible defaults"
```

---

### Task 3: HTTP client — auth + user agent

**Files:**
- Create: `local_story_archive/client.py`
- Create: `tests/unit/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
from local_story_archive.client import build_client
from local_story_archive.config import Config
from pathlib import Path


def test_client_sets_user_agent(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="abc", user_agent="ua/1")
    client = build_client(cfg)
    try:
        assert client.headers["User-Agent"] == "ua/1"
    finally:
        client.close()


def test_client_attaches_cookie(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="my-token")
    client = build_client(cfg)
    try:
        assert client.cookies.get("token") == "my-token"
    finally:
        client.close()


def test_client_no_cookie_when_empty(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="")
    client = build_client(cfg)
    try:
        assert client.cookies.get("token") is None
    finally:
        client.close()
```

- [ ] **Step 2: Run test — should fail** (no module).

- [ ] **Step 3: Implement `local_story_archive/client.py`**

```python
import httpx
from local_story_archive.config import Config


def build_client(cfg: Config) -> httpx.Client:
    cookies: dict[str, str] = {}
    if cfg.cookie:
        cookies["token"] = cfg.cookie
    return httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        cookies=cookies,
        timeout=30.0,
        follow_redirects=True,
    )
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/client.py tests/unit/test_client.py
git commit -m "feat(client): httpx client with cookie auth"
```

---

### Task 4: Rate limiter (token bucket)

**Files:**
- Modify: `local_story_archive/client.py`
- Modify: `tests/unit/test_client.py`

- [ ] **Step 1: Add the failing test** to `tests/unit/test_client.py`

```python
import time
from local_story_archive.client import TokenBucket


def test_token_bucket_blocks_when_empty():
    bucket = TokenBucket(rate_per_sec=10.0, capacity=2)
    bucket.take()
    bucket.take()
    start = time.monotonic()
    bucket.take()  # should sleep ~0.1s
    elapsed = time.monotonic() - start
    assert 0.05 < elapsed < 0.3


def test_token_bucket_does_not_block_when_full():
    bucket = TokenBucket(rate_per_sec=1.0, capacity=3)
    start = time.monotonic()
    for _ in range(3):
        bucket.take()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05
```

- [ ] **Step 2: Run — should fail** (no `TokenBucket`).

- [ ] **Step 3: Add `TokenBucket` to `local_story_archive/client.py`**

Append to `client.py`:

```python
import threading
import time


class TokenBucket:
    """Simple thread-safe token bucket. Blocks on take() when empty."""

    def __init__(self, rate_per_sec: float, capacity: int = 5):
        self.rate = rate_per_sec
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                sleep_for = deficit / self.rate
            # release lock during sleep, retry
        time.sleep(sleep_for)
        self.take(n)
```

(Note the structural quirk: we sleep *outside* the lock then retry, so other threads aren't blocked while one waits.)

- [ ] **Step 4: Run tests — both should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/client.py tests/unit/test_client.py
git commit -m "feat(client): token-bucket rate limiter"
```

---

### Task 5: Retry-aware GET wrapper

**Files:**
- Modify: `local_story_archive/client.py`
- Modify: `tests/unit/test_client.py`

- [ ] **Step 1: Add failing tests**

```python
import httpx
import pytest
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import Config


def make_client(tmp_path, transport):
    cfg = Config(output_dir=tmp_path, rate_limit_per_sec=1000.0)
    rlc = RateLimitedClient(cfg)
    rlc._client = httpx.Client(transport=transport, headers={"User-Agent": cfg.user_agent})
    return rlc


def test_client_retries_on_5xx(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    r = rlc.get("https://example.com/x")
    assert r.status_code == 200
    assert calls["n"] == 3
    rlc.close()


def test_client_gives_up_after_max_attempts(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    rlc = make_client(tmp_path, transport)
    with pytest.raises(httpx.HTTPStatusError):
        rlc.get("https://example.com/x", max_attempts=3)
    rlc.close()


def test_client_honors_retry_after_on_429(tmp_path):
    import time
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    start = time.monotonic()
    r = rlc.get("https://example.com/x")
    assert r.status_code == 200
    assert (time.monotonic() - start) >= 0.9
    rlc.close()
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `RateLimitedClient`** — append to `local_story_archive/client.py`

```python
import logging

logger = logging.getLogger(__name__)


class RateLimitedClient:
    def __init__(self, cfg: Config):
        self._client = build_client(cfg)
        self._bucket = TokenBucket(cfg.rate_limit_per_sec, capacity=max(2, int(cfg.rate_limit_per_sec * 2)))

    def get(self, url: str, *, max_attempts: int = 5, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            self._bucket.take()
            try:
                resp = self._client.get(url, **kwargs)
            except httpx.RequestError as e:
                last_exc = e
                self._sleep_backoff(attempt)
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", "60"))
                logger.warning("429 on %s — sleeping %.1fs", url, wait)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                self._sleep_backoff(attempt)
                continue
            resp.raise_for_status()
            return resp
        if last_exc:
            raise last_exc
        resp.raise_for_status()
        return resp

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(2 ** (attempt - 1), 16))

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/client.py tests/unit/test_client.py
git commit -m "feat(client): retry on 5xx, honor Retry-After on 429"
```

---

## Phase 2 — Models & Manifest

### Task 6: Dataclass models

**Files:**
- Create: `local_story_archive/models.py`

This task has no test of its own — models are exercised by every later test. Just define them.

- [ ] **Step 1: Create `local_story_archive/models.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


PartStatus = Literal[
    "pending", "in_progress", "done", "failed", "body_text_failed", "gone", "private"
]
StoryStatus = Literal[
    "pending", "in_progress", "done", "failed", "gone", "private"
]


@dataclass
class Part:
    part_id: str
    ordinal: int
    title: str
    url: str
    last_modified: str | None = None


@dataclass
class Story:
    story_id: str
    title: str
    author_username: str
    description: str = ""
    cover_url: str = ""
    tags: list[str] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    last_modified: str | None = None
    votes: int = 0
    reads: int = 0
    completed: bool = False


@dataclass
class Comment:
    comment_id: str
    user: str
    body: str
    created_at: str
    paragraph_id: str | None = None     # set for inline comments
    replies: list["Comment"] = field(default_factory=list)
```

- [ ] **Step 2: Smoke check**

```bash
python -c "from local_story_archive.models import Story, Part, Comment; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add local_story_archive/models.py
git commit -m "feat(models): Story/Part/Comment dataclasses"
```

---

### Task 7: SQLite manifest schema + connect

**Files:**
- Create: `local_story_archive/archive/__init__.py` (empty)
- Create: `local_story_archive/archive/state.py`
- Create: `tests/unit/test_state.py`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
from local_story_archive.archive.state import Manifest


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
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `local_story_archive/archive/state.py`**

```python
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
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/archive/__init__.py local_story_archive/archive/state.py tests/unit/test_state.py
git commit -m "feat(state): SQLite manifest schema"
```

---

### Task 8: Manifest — story & part status CRUD

**Files:**
- Modify: `local_story_archive/archive/state.py`
- Modify: `tests/unit/test_state.py`

- [ ] **Step 1: Add failing tests**

```python
from local_story_archive.models import Story, Part


def make_story(story_id="s1") -> Story:
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
    m.upsert_story(make_story())
    m.upsert_parts(make_story())
    m.set_part_status("s1", "p1", "in_progress")
    m.set_part_status("s1", "p1", "done", body_hash="abc")
    p = m.get_part("s1", "p1")
    assert p["status"] == "done"
    assert p["body_hash"] == "abc"
    m.close()


def test_pending_parts_query(output_dir: Path):
    m = Manifest(output_dir).connect()
    m.upsert_story(make_story())
    m.upsert_parts(make_story())
    m.set_part_status("s1", "p1", "done")
    pending = m.pending_parts_for("s1")
    assert [p["part_id"] for p in pending] == ["p2"]
    m.close()
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append CRUD methods to `Manifest`** in `local_story_archive/archive/state.py`

```python
    # --- story / part CRUD ---

    def upsert_story(self, story: Story) -> None:
        assert self.conn
        self.conn.execute(
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
        self.conn.commit()

    def upsert_parts(self, story: Story) -> None:
        assert self.conn
        self.conn.executemany(
            """
            INSERT INTO parts(story_id, part_id, ordinal)
            VALUES (?, ?, ?)
            ON CONFLICT(story_id, part_id) DO UPDATE SET ordinal = excluded.ordinal
            """,
            [(story.story_id, p.part_id, p.ordinal) for p in story.parts],
        )
        self.conn.commit()

    def set_part_status(
        self,
        story_id: str,
        part_id: str,
        status: str,
        *,
        body_hash: str | None = None,
        last_error: str | None = None,
    ) -> None:
        assert self.conn
        self.conn.execute(
            """
            UPDATE parts SET status = ?,
                             body_hash = COALESCE(?, body_hash),
                             last_error = ?
            WHERE story_id = ? AND part_id = ?
            """,
            (status, body_hash, last_error, story_id, part_id),
        )
        self.conn.commit()

    def get_story(self, story_id: str) -> dict | None:
        assert self.conn
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            "SELECT * FROM stories WHERE story_id = ?", (story_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_part(self, story_id: str, part_id: str) -> dict | None:
        assert self.conn
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            "SELECT * FROM parts WHERE story_id = ? AND part_id = ?",
            (story_id, part_id),
        ).fetchone()
        return dict(row) if row else None

    def pending_parts_for(self, story_id: str) -> list[dict]:
        assert self.conn
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            """
            SELECT * FROM parts
            WHERE story_id = ? AND status NOT IN ('done', 'gone', 'private')
            ORDER BY ordinal
            """,
            (story_id,),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/archive/state.py tests/unit/test_state.py
git commit -m "feat(state): story/part upsert + status transitions"
```

---

## Phase 3 — API Parsers

### Task 9: API — story metadata + parts list

**Files:**
- Create: `local_story_archive/api/__init__.py` (empty)
- Create: `local_story_archive/api/story.py`
- Create: `tests/fixtures/api_responses/story_metadata.json`
- Create: `tests/unit/test_api_story.py`

- [ ] **Step 1: Create the fixture** — `tests/fixtures/api_responses/story_metadata.json`

```json
{
  "id": "123456789",
  "title": "Shadow & Bone Rewrite",
  "user": { "name": "alice" },
  "description": "What if the Darkling won?",
  "cover": "https://img.wattpad.com/cover/123.jpg",
  "tags": ["grishaverse", "fantasy"],
  "modifyDate": "2026-04-30T10:00:00Z",
  "voteCount": 1234,
  "readCount": 56789,
  "completed": false,
  "parts": [
    {
      "id": "1001",
      "title": "Chapter One",
      "url": "https://www.wattpad.com/1001-chapter-one",
      "modifyDate": "2026-04-29T10:00:00Z"
    },
    {
      "id": "1002",
      "title": "Chapter Two",
      "url": "https://www.wattpad.com/1002-chapter-two",
      "modifyDate": "2026-04-30T10:00:00Z"
    }
  ]
}
```

- [ ] **Step 2: Write failing tests** — `tests/unit/test_api_story.py`

```python
import json
from pathlib import Path
from local_story_archive.api.story import parse_story


def test_parse_story_basic(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/story_metadata.json").read_text())
    s = parse_story(raw)
    assert s.story_id == "123456789"
    assert s.title == "Shadow & Bone Rewrite"
    assert s.author_username == "alice"
    assert s.tags == ["grishaverse", "fantasy"]
    assert s.votes == 1234
    assert s.reads == 56789
    assert s.completed is False
    assert len(s.parts) == 2
    assert s.parts[0].part_id == "1001"
    assert s.parts[0].ordinal == 1
    assert s.parts[1].ordinal == 2
```

- [ ] **Step 3: Run — should fail.**

- [ ] **Step 4: Implement `local_story_archive/api/story.py`**

```python
from typing import Any
from local_story_archive.models import Story, Part
from local_story_archive.client import RateLimitedClient


STORY_FIELDS = (
    "id,title,user,description,cover,tags,modifyDate,voteCount,readCount,completed,"
    "parts(id,title,url,modifyDate)"
)
STORY_URL = "https://www.wattpad.com/api/v3/stories/{story_id}?fields=" + STORY_FIELDS


def parse_story(raw: dict[str, Any]) -> Story:
    parts = [
        Part(
            part_id=str(p["id"]),
            ordinal=i + 1,
            title=p.get("title", ""),
            url=p.get("url", ""),
            last_modified=p.get("modifyDate"),
        )
        for i, p in enumerate(raw.get("parts", []))
    ]
    return Story(
        story_id=str(raw["id"]),
        title=raw.get("title", ""),
        author_username=raw.get("user", {}).get("name", ""),
        description=raw.get("description", ""),
        cover_url=raw.get("cover", ""),
        tags=list(raw.get("tags", [])),
        parts=parts,
        last_modified=raw.get("modifyDate"),
        votes=int(raw.get("voteCount", 0)),
        reads=int(raw.get("readCount", 0)),
        completed=bool(raw.get("completed", False)),
    )


def fetch_story(client: RateLimitedClient, story_id: str) -> Story:
    resp = client.get(STORY_URL.format(story_id=story_id))
    return parse_story(resp.json())
```

- [ ] **Step 5: Run tests — should pass.**

- [ ] **Step 6: Commit**

```bash
git add local_story_archive/api/__init__.py local_story_archive/api/story.py tests/fixtures/api_responses/story_metadata.json tests/unit/test_api_story.py
git commit -m "feat(api): story metadata fetch + parser"
```

---

### Task 10: API — user library, lists, favorites

**Files:**
- Create: `local_story_archive/api/user.py`
- Create: `tests/fixtures/api_responses/library.json`
- Create: `tests/fixtures/api_responses/reading_lists.json`
- Create: `tests/unit/test_api_user.py`

- [ ] **Step 1: Create fixture** `tests/fixtures/api_responses/library.json`

```json
{
  "stories": [
    {"id": "111", "title": "First"},
    {"id": "222", "title": "Second"},
    {"id": "333", "title": "Third"}
  ],
  "total": 3
}
```

- [ ] **Step 2: Create fixture** `tests/fixtures/api_responses/reading_lists.json`

```json
{
  "lists": [
    {"id": "L1", "name": "Favorites", "numStories": 2},
    {"id": "L2", "name": "To Read", "numStories": 5}
  ]
}
```

- [ ] **Step 3: Write failing tests**

```python
import json
from pathlib import Path
from local_story_archive.api.user import parse_library, parse_reading_lists


def test_parse_library_returns_story_ids(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/library.json").read_text())
    ids = parse_library(raw)
    assert ids == ["111", "222", "333"]


def test_parse_reading_lists(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/reading_lists.json").read_text())
    lists = parse_reading_lists(raw)
    assert len(lists) == 2
    assert lists[0]["id"] == "L1"
    assert lists[0]["name"] == "Favorites"
```

- [ ] **Step 4: Run — should fail.**

- [ ] **Step 5: Implement `local_story_archive/api/user.py`**

```python
from typing import Any
from local_story_archive.client import RateLimitedClient


LIBRARY_URL = "https://www.wattpad.com/api/v3/users/{username}/library?limit=200"
READING_LISTS_URL = "https://www.wattpad.com/api/v3/users/{username}/lists"
LIST_STORIES_URL = "https://www.wattpad.com/api/v3/lists/{list_id}/stories?limit=500"


def parse_library(raw: dict[str, Any]) -> list[str]:
    return [str(s["id"]) for s in raw.get("stories", [])]


def parse_reading_lists(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": str(L["id"]), "name": L.get("name", ""), "num_stories": L.get("numStories", 0)}
        for L in raw.get("lists", [])
    ]


def fetch_library(client: RateLimitedClient, username: str) -> list[str]:
    ids: list[str] = []
    url = LIBRARY_URL.format(username=username)
    while url:
        data = client.get(url).json()
        ids.extend(parse_library(data))
        url = data.get("nextUrl") or data.get("nextPage") or ""
    return ids


def fetch_reading_lists(client: RateLimitedClient, username: str) -> list[dict[str, Any]]:
    return parse_reading_lists(client.get(READING_LISTS_URL.format(username=username)).json())


def fetch_list_story_ids(client: RateLimitedClient, list_id: str) -> list[str]:
    ids: list[str] = []
    url = LIST_STORIES_URL.format(list_id=list_id)
    while url:
        data = client.get(url).json()
        ids.extend(str(s["id"]) for s in data.get("stories", []))
        url = data.get("nextUrl") or ""
    return ids
```

- [ ] **Step 6: Run tests — should pass.**

- [ ] **Step 7: Commit**

```bash
git add local_story_archive/api/user.py tests/fixtures/api_responses/*.json tests/unit/test_api_user.py
git commit -m "feat(api): library + reading lists endpoints"
```

---

### Task 11: API — comments (inline + end-of-part)

**Files:**
- Create: `local_story_archive/api/comments.py`
- Create: `tests/fixtures/api_responses/comments_page.json`
- Create: `tests/unit/test_api_comments.py`

- [ ] **Step 1: Create fixture** `tests/fixtures/api_responses/comments_page.json`

```json
{
  "comments": [
    {
      "id": "c1",
      "user": {"name": "bob"},
      "body": "Loved this chapter!",
      "createdAt": "2026-04-30T11:00:00Z",
      "paragraphId": "p_42",
      "replies": [
        {
          "id": "c1r1",
          "user": {"name": "alice"},
          "body": "Thanks!",
          "createdAt": "2026-04-30T12:00:00Z",
          "paragraphId": null,
          "replies": []
        }
      ]
    },
    {
      "id": "c2",
      "user": {"name": "carol"},
      "body": "When's the next?",
      "createdAt": "2026-04-30T13:00:00Z",
      "paragraphId": null,
      "replies": []
    }
  ],
  "nextUrl": null
}
```

- [ ] **Step 2: Write failing tests**

```python
import json
from pathlib import Path
from local_story_archive.api.comments import parse_comments_page


def test_parse_comments_page(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/comments_page.json").read_text())
    comments, next_url = parse_comments_page(raw)
    assert len(comments) == 2
    assert comments[0].comment_id == "c1"
    assert comments[0].user == "bob"
    assert comments[0].paragraph_id == "p_42"
    assert len(comments[0].replies) == 1
    assert comments[0].replies[0].user == "alice"
    assert next_url is None
```

- [ ] **Step 3: Run — should fail.**

- [ ] **Step 4: Implement `local_story_archive/api/comments.py`**

```python
from typing import Any
from local_story_archive.client import RateLimitedClient
from local_story_archive.models import Comment


INLINE_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100"
END_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100&forms=root"


def _parse_one(raw: dict[str, Any]) -> Comment:
    return Comment(
        comment_id=str(raw["id"]),
        user=raw.get("user", {}).get("name", ""),
        body=raw.get("body", ""),
        created_at=raw.get("createdAt", ""),
        paragraph_id=raw.get("paragraphId"),
        replies=[_parse_one(r) for r in raw.get("replies", [])],
    )


def parse_comments_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    comments = [_parse_one(c) for c in raw.get("comments", [])]
    return comments, raw.get("nextUrl")


def _fetch_all(client: RateLimitedClient, url: str) -> list[Comment]:
    out: list[Comment] = []
    while url:
        data = client.get(url).json()
        comments, next_url = parse_comments_page(data)
        out.extend(comments)
        url = next_url or ""
    return out


def fetch_inline_comments(client: RateLimitedClient, part_id: str) -> list[Comment]:
    return _fetch_all(client, INLINE_URL.format(part_id=part_id))


def fetch_end_comments(client: RateLimitedClient, part_id: str) -> list[Comment]:
    return _fetch_all(client, END_URL.format(part_id=part_id))
```

- [ ] **Step 5: Run tests — should pass.**

- [ ] **Step 6: Commit**

```bash
git add local_story_archive/api/comments.py tests/fixtures/api_responses/comments_page.json tests/unit/test_api_comments.py
git commit -m "feat(api): paginated comments fetcher"
```

---

## Phase 4 — Chapter HTML Scraper

### Task 12: Parse chapter HTML for body + image refs

**Files:**
- Create: `local_story_archive/scrape/__init__.py` (empty)
- Create: `local_story_archive/scrape/chapter_html.py`
- Create: `tests/fixtures/html_chapters/chapter_with_images.html`
- Create: `tests/unit/test_chapter_html.py`

- [ ] **Step 1: Create fixture** `tests/fixtures/html_chapters/chapter_with_images.html`

```html
<!doctype html>
<html><body>
<header>nav junk</header>
<div class="page-container">
  <pre data-p-id="p1">First paragraph of <b>bold</b> text.</pre>
  <pre data-p-id="p2">Second <i>italic</i> paragraph.</pre>
  <pre data-p-id="p3"><img src="https://img.wattpad.com/inline/abc.jpg" alt="scene"></pre>
  <pre data-p-id="p4">Last paragraph.</pre>
</div>
<footer>more junk</footer>
</body></html>
```

- [ ] **Step 2: Write failing tests**

```python
from pathlib import Path
from local_story_archive.scrape.chapter_html import extract_chapter


def test_extract_chapter_body_text(fixtures_dir: Path):
    html = (fixtures_dir / "html_chapters/chapter_with_images.html").read_text()
    result = extract_chapter(html)
    assert "First paragraph of bold text." in result.text
    assert "Second italic paragraph." in result.text
    assert "Last paragraph." in result.text
    assert "nav junk" not in result.text
    assert "more junk" not in result.text


def test_extract_chapter_paragraph_count(fixtures_dir: Path):
    html = (fixtures_dir / "html_chapters/chapter_with_images.html").read_text()
    result = extract_chapter(html)
    assert len(result.paragraphs) == 4
    assert result.paragraphs[0]["id"] == "p1"


def test_extract_chapter_images(fixtures_dir: Path):
    html = (fixtures_dir / "html_chapters/chapter_with_images.html").read_text()
    result = extract_chapter(html)
    assert result.images == ["https://img.wattpad.com/inline/abc.jpg"]
```

- [ ] **Step 3: Run — should fail.**

- [ ] **Step 4: Implement `local_story_archive/scrape/chapter_html.py`**

```python
from dataclasses import dataclass
from bs4 import BeautifulSoup


@dataclass
class ChapterContent:
    text: str
    paragraphs: list[dict]    # [{"id": str, "text": str, "html": str}]
    images: list[str]


def extract_chapter(html: str) -> ChapterContent:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(".page-container") or soup.body
    paragraphs: list[dict] = []
    images: list[str] = []
    for pre in container.find_all("pre"):
        pid = pre.get("data-p-id", "")
        for img in pre.find_all("img"):
            src = img.get("src", "")
            if src:
                images.append(src)
        paragraphs.append({
            "id": pid,
            "text": pre.get_text("", strip=True),
            "html": pre.decode_contents(),
        })
    text = "\n\n".join(p["text"] for p in paragraphs if p["text"])
    return ChapterContent(text=text, paragraphs=paragraphs, images=images)
```

- [ ] **Step 5: Run tests — should pass.**

- [ ] **Step 6: Commit**

```bash
git add local_story_archive/scrape/__init__.py local_story_archive/scrape/chapter_html.py tests/fixtures/html_chapters/chapter_with_images.html tests/unit/test_chapter_html.py
git commit -m "feat(scrape): parse chapter HTML body + images"
```

---

## Phase 5 — Archive Store

### Task 13: Atomic write helper + directory layout

**Files:**
- Create: `local_story_archive/archive/store.py`
- Create: `tests/unit/test_store.py`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
from local_story_archive.archive.store import (
    atomic_write_text, atomic_write_bytes, story_dir, slugify,
)
from local_story_archive.models import Story


def test_slugify_basic():
    assert slugify("Shadow & Bone: Rewrite!") == "shadow-bone-rewrite"
    assert slugify("  multi   space  ") == "multi-space"
    assert slugify("CAPS") == "caps"


def test_atomic_write_text(output_dir: Path):
    target = output_dir / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites(output_dir: Path):
    target = output_dir / "x.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"


def test_story_dir_layout(output_dir: Path):
    s = Story(story_id="42", title="Hi There!", author_username="bob")
    d = story_dir(output_dir, s)
    assert d == output_dir / "stories" / "bob" / "42_hi-there"
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `local_story_archive/archive/store.py`**

```python
import os
import re
from pathlib import Path
from local_story_archive.models import Story


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    s = s.lower()
    s = _SLUG_RE.sub("-", s)
    return s.strip("-")


def atomic_write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def story_dir(output_dir: Path, story: Story) -> Path:
    return output_dir / "stories" / story.author_username / f"{story.story_id}_{slugify(story.title)}"
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/archive/store.py tests/unit/test_store.py
git commit -m "feat(store): atomic writes + slugified story dir"
```

---

### Task 14: Write part files (json/html/txt + comments)

**Files:**
- Modify: `local_story_archive/archive/store.py`
- Modify: `tests/unit/test_store.py`

- [ ] **Step 1: Add failing tests**

```python
from dataclasses import asdict
from local_story_archive.archive.store import write_part_files
from local_story_archive.models import Story, Part, Comment
from local_story_archive.scrape.chapter_html import ChapterContent


def test_write_part_files_creates_all_artifacts(output_dir: Path):
    s = Story(
        story_id="42",
        title="Hi There",
        author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="Chapter One", url="https://w/100")],
    )
    content = ChapterContent(
        text="Once upon a time.",
        paragraphs=[{"id": "p1", "text": "Once upon a time.", "html": "Once upon a time."}],
        images=[],
    )
    inline = [Comment(comment_id="c1", user="bob", body="!", created_at="t", paragraph_id="p1")]
    end = [Comment(comment_id="c2", user="alice", body="!", created_at="t")]
    write_part_files(output_dir, s, s.parts[0], content, "<html>...</html>", inline, end)

    base = output_dir / "stories" / "bob" / "42_hi-there" / "parts"
    assert (base / "01_100_chapter-one.json").exists()
    assert (base / "01_100_chapter-one.html").exists()
    assert (base / "01_100_chapter-one.txt").exists()
    assert (base / "01_100_comments-inline.json").exists()
    assert (base / "01_100_comments-end.json").exists()
    assert (base / "01_100_chapter-one.txt").read_text() == "Once upon a time."
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append to `local_story_archive/archive/store.py`**

```python
import json
from dataclasses import asdict
from local_story_archive.models import Comment, Part
from local_story_archive.scrape.chapter_html import ChapterContent


def _part_basename(part: Part) -> str:
    return f"{part.ordinal:02d}_{part.part_id}_{slugify(part.title)}"


def _comments_basename(part: Part) -> str:
    return f"{part.ordinal:02d}_{part.part_id}"


def write_part_files(
    output_dir: Path,
    story: Story,
    part: Part,
    content: ChapterContent,
    raw_html: str,
    inline_comments: list[Comment],
    end_comments: list[Comment],
) -> None:
    parts_dir = story_dir(output_dir, story) / "parts"
    base = _part_basename(part)
    cbase = _comments_basename(part)
    atomic_write_text(parts_dir / f"{base}.json", json.dumps({
        "part_id": part.part_id,
        "ordinal": part.ordinal,
        "title": part.title,
        "url": part.url,
        "last_modified": part.last_modified,
        "paragraphs": content.paragraphs,
        "images": content.images,
    }, ensure_ascii=False, indent=2))
    atomic_write_text(parts_dir / f"{base}.html", raw_html)
    atomic_write_text(parts_dir / f"{base}.txt", content.text)
    atomic_write_text(
        parts_dir / f"{cbase}_comments-inline.json",
        json.dumps([_comment_to_dict(c) for c in inline_comments], ensure_ascii=False, indent=2),
    )
    atomic_write_text(
        parts_dir / f"{cbase}_comments-end.json",
        json.dumps([_comment_to_dict(c) for c in end_comments], ensure_ascii=False, indent=2),
    )


def _comment_to_dict(c: Comment) -> dict:
    return {
        "comment_id": c.comment_id,
        "user": c.user,
        "body": c.body,
        "created_at": c.created_at,
        "paragraph_id": c.paragraph_id,
        "replies": [_comment_to_dict(r) for r in c.replies],
    }
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/archive/store.py tests/unit/test_store.py
git commit -m "feat(store): write per-part json/html/txt + comments"
```

---

### Task 15: Write story metadata + cover

**Files:**
- Modify: `local_story_archive/archive/store.py`
- Modify: `tests/unit/test_store.py`

- [ ] **Step 1: Add failing tests**

```python
def test_write_story_metadata(output_dir: Path):
    from local_story_archive.archive.store import write_story_metadata
    s = Story(
        story_id="42",
        title="Hi There",
        author_username="bob",
        description="d",
        cover_url="https://x/c.jpg",
        tags=["a", "b"],
    )
    write_story_metadata(output_dir, s)
    p = output_dir / "stories" / "bob" / "42_hi-there" / "metadata.json"
    data = json.loads(p.read_text())
    assert data["story_id"] == "42"
    assert data["tags"] == ["a", "b"]


def test_write_cover_bytes(output_dir: Path):
    from local_story_archive.archive.store import write_cover
    s = Story(story_id="42", title="Hi There", author_username="bob")
    write_cover(output_dir, s, b"FAKEJPGBYTES")
    p = output_dir / "stories" / "bob" / "42_hi-there" / "cover.jpg"
    assert p.read_bytes() == b"FAKEJPGBYTES"
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append to `local_story_archive/archive/store.py`**

```python
def write_story_metadata(output_dir: Path, story: Story) -> None:
    sd = story_dir(output_dir, story)
    payload = {
        "story_id": story.story_id,
        "title": story.title,
        "author_username": story.author_username,
        "description": story.description,
        "cover_url": story.cover_url,
        "tags": story.tags,
        "last_modified": story.last_modified,
        "votes": story.votes,
        "reads": story.reads,
        "completed": story.completed,
        "parts": [
            {
                "part_id": p.part_id,
                "ordinal": p.ordinal,
                "title": p.title,
                "url": p.url,
                "last_modified": p.last_modified,
            } for p in story.parts
        ],
    }
    atomic_write_text(sd / "metadata.json", json.dumps(payload, ensure_ascii=False, indent=2))


def write_cover(output_dir: Path, story: Story, data: bytes) -> None:
    atomic_write_bytes(story_dir(output_dir, story) / "cover.jpg", data)
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/archive/store.py tests/unit/test_store.py
git commit -m "feat(store): write story metadata.json + cover.jpg"
```

---

## Phase 6 — Renderers

### Task 16: TXT renderer

**Files:**
- Create: `local_story_archive/render/__init__.py` (empty)
- Create: `local_story_archive/render/txt.py`
- Create: `tests/unit/test_render_txt.py`

- [ ] **Step 1: Write failing test**

```python
from pathlib import Path
import json
from local_story_archive.render.txt import render_txt


def test_render_txt_concatenates_chapters_in_order(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi" / "parts"
    sd.mkdir(parents=True)
    (sd / "01_100_one.txt").write_text("Chapter one body.")
    (sd / "02_101_two.txt").write_text("Chapter two body.")
    (sd.parent / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [
            {"part_id": "100", "ordinal": 1, "title": "One"},
            {"part_id": "101", "ordinal": 2, "title": "Two"},
        ],
    }))
    out = render_txt(sd.parent)
    assert "Chapter one body." in out
    assert "Chapter two body." in out
    assert out.index("Chapter one body.") < out.index("Chapter two body.")
    assert "One" in out  # chapter title appears
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `local_story_archive/render/txt.py`**

```python
import json
from pathlib import Path


def render_txt(story_dir_path: Path) -> str:
    meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
    parts_dir = story_dir_path / "parts"
    chunks = [f"{meta['title']}\nby {meta['author_username']}\n\n"]
    for p in sorted(meta["parts"], key=lambda x: x["ordinal"]):
        ord_ = int(p["ordinal"])
        # find the matching .txt file by ordinal + part_id prefix
        prefix = f"{ord_:02d}_{p['part_id']}_"
        candidates = list(parts_dir.glob(f"{prefix}*.txt"))
        if not candidates:
            continue
        body = candidates[0].read_text(encoding="utf-8")
        chunks.append(f"\n\n========\n{p['title']}\n========\n\n{body}\n")
    full = "".join(chunks)
    out_path = story_dir_path / "output" / f"{story_dir_path.name.split('_', 1)[1]}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full, encoding="utf-8")
    return full
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/render/__init__.py local_story_archive/render/txt.py tests/unit/test_render_txt.py
git commit -m "feat(render): plain text renderer"
```

---

### Task 17: HTML renderer

**Files:**
- Create: `local_story_archive/render/html.py`
- Create: `tests/unit/test_render_html.py`

- [ ] **Step 1: Write failing test**

```python
from pathlib import Path
import json
from local_story_archive.render.html import render_html


def test_render_html_single_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.html").write_text(
        '<pre data-p-id="x">First.</pre><pre data-p-id="y">Second.</pre>'
    )
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "Chapter One"}],
    }))
    out = render_html(sd)
    assert "<title>Hi</title>" in out
    assert "Chapter One" in out
    assert "First." in out
    assert "Second." in out
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `local_story_archive/render/html.py`**

```python
import html
import json
from pathlib import Path


_HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:Georgia,serif;max-width:42em;margin:2em auto;padding:0 1em;line-height:1.6;}}
h1,h2{{font-family:system-ui,sans-serif;}}
hr{{border:none;border-top:1px solid #ccc;margin:3em 0;}}
.chapter{{margin-bottom:4em;}}
</style></head><body>"""


def render_html(story_dir_path: Path) -> str:
    meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
    parts_dir = story_dir_path / "parts"
    body_chunks = [
        f"<h1>{html.escape(meta['title'])}</h1>",
        f"<p><em>by {html.escape(meta['author_username'])}</em></p>",
    ]
    for p in sorted(meta["parts"], key=lambda x: x["ordinal"]):
        prefix = f"{int(p['ordinal']):02d}_{p['part_id']}_"
        candidates = list(parts_dir.glob(f"{prefix}*.html"))
        if not candidates:
            continue
        chapter_html = candidates[0].read_text(encoding="utf-8")
        body_chunks.append('<hr><div class="chapter">')
        body_chunks.append(f"<h2>{html.escape(p['title'])}</h2>")
        body_chunks.append(chapter_html)
        body_chunks.append("</div>")
    full = _HEAD.format(title=html.escape(meta["title"])) + "\n".join(body_chunks) + "</body></html>"
    out_path = story_dir_path / "output" / f"{story_dir_path.name.split('_', 1)[1]}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full, encoding="utf-8")
    return full
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/render/html.py tests/unit/test_render_html.py
git commit -m "feat(render): single-file standalone HTML renderer"
```

---

### Task 18: EPUB renderer

**Files:**
- Create: `local_story_archive/render/epub.py`
- Create: `tests/unit/test_render_epub.py`

- [ ] **Step 1: Write failing test**

```python
from pathlib import Path
import json
from local_story_archive.render.epub import render_epub


def test_render_epub_creates_file(output_dir: Path):
    sd = output_dir / "stories" / "bob" / "42_hi"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.html").write_text("<pre>Chapter one body.</pre>")
    (sd / "parts" / "02_101_two.html").write_text("<pre>Chapter two body.</pre>")
    (sd / "metadata.json").write_text(json.dumps({
        "title": "Hi", "author_username": "bob", "story_id": "42",
        "tags": ["x"], "description": "d",
        "parts": [
            {"part_id": "100", "ordinal": 1, "title": "Chapter One"},
            {"part_id": "101", "ordinal": 2, "title": "Chapter Two"},
        ],
    }))
    out_path = render_epub(sd)
    assert out_path.exists()
    assert out_path.suffix == ".epub"
    assert out_path.stat().st_size > 0
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `local_story_archive/render/epub.py`**

```python
import json
from pathlib import Path
from ebooklib import epub


def render_epub(story_dir_path: Path) -> Path:
    meta = json.loads((story_dir_path / "metadata.json").read_text(encoding="utf-8"))
    parts_dir = story_dir_path / "parts"
    book = epub.EpubBook()
    book.set_identifier(f"wattpad-{meta['story_id']}")
    book.set_title(meta["title"])
    book.set_language("en")
    book.add_author(meta["author_username"])
    if meta.get("description"):
        book.add_metadata("DC", "description", meta["description"])
    cover_path = story_dir_path / "cover.jpg"
    if cover_path.exists():
        book.set_cover("cover.jpg", cover_path.read_bytes())

    chapters = []
    for p in sorted(meta["parts"], key=lambda x: x["ordinal"]):
        prefix = f"{int(p['ordinal']):02d}_{p['part_id']}_"
        candidates = list(parts_dir.glob(f"{prefix}*.html"))
        if not candidates:
            continue
        body = candidates[0].read_text(encoding="utf-8")
        ch = epub.EpubHtml(
            title=p["title"],
            file_name=f"chap_{int(p['ordinal']):02d}.xhtml",
            lang="en",
        )
        ch.content = f"<h1>{p['title']}</h1>\n{body}"
        book.add_item(ch)
        chapters.append(ch)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]

    out_dir = story_dir_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{story_dir_path.name.split('_', 1)[1]}.epub"
    epub.write_epub(str(out_path), book)
    return out_path
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/render/epub.py tests/unit/test_render_epub.py
git commit -m "feat(render): EPUB renderer via EbookLib"
```

---

## Phase 7 — Job Orchestration

### Task 19: Single-story archive job

**Files:**
- Create: `local_story_archive/jobs.py`
- Create: `tests/unit/test_jobs.py`

- [ ] **Step 1: Write failing test (uses fakes; no network)**

```python
from pathlib import Path
from unittest.mock import MagicMock
from local_story_archive.jobs import archive_story
from local_story_archive.models import Story, Part, Comment
from local_story_archive.archive.state import Manifest
from local_story_archive.scrape.chapter_html import ChapterContent
from local_story_archive.config import Config


def test_archive_story_writes_all_artifacts(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()

    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    # Stub the four functions used by archive_story:
    deps = MagicMock()
    deps.fetch_story.return_value = story
    deps.fetch_chapter_html.return_value = "<pre>One body.</pre>"
    deps.parse_chapter.return_value = ChapterContent(
        text="One body.",
        paragraphs=[{"id": "p1", "text": "One body.", "html": "One body."}],
        images=[],
    )
    deps.fetch_inline_comments.return_value = []
    deps.fetch_end_comments.return_value = []
    deps.fetch_cover_bytes.return_value = b""

    archive_story(cfg, fake_client, manifest, "42", deps=deps)

    sd = output_dir / "stories" / "bob" / "42_hi"
    assert (sd / "metadata.json").exists()
    assert (sd / "parts" / "01_100_one.json").exists()
    assert (sd / "parts" / "01_100_one.txt").exists()
    assert (sd / "output").exists()  # renders ran
    row = manifest.get_part("42", "100")
    assert row["status"] == "done"
    manifest.close()
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `local_story_archive/jobs.py`**

```python
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from local_story_archive.config import Config
from local_story_archive.client import RateLimitedClient
from local_story_archive.archive.state import Manifest
from local_story_archive.archive import store
from local_story_archive.models import Story
from local_story_archive.scrape.chapter_html import extract_chapter, ChapterContent
from local_story_archive.api import story as api_story
from local_story_archive.api import comments as api_comments
from local_story_archive.render import txt as render_txt
from local_story_archive.render import html as render_html
from local_story_archive.render import epub as render_epub

logger = logging.getLogger(__name__)


@dataclass
class JobDeps:
    """Indirection layer so tests can inject fakes."""
    fetch_story: Callable
    fetch_chapter_html: Callable
    parse_chapter: Callable
    fetch_inline_comments: Callable
    fetch_end_comments: Callable
    fetch_cover_bytes: Callable


def _default_deps() -> JobDeps:
    def fetch_chapter_html(client: RateLimitedClient, url: str) -> str:
        return client.get(url).text

    def fetch_cover_bytes(client: RateLimitedClient, url: str) -> bytes:
        if not url:
            return b""
        try:
            return client.get(url).content
        except Exception as e:
            logger.warning("cover fetch failed: %s", e)
            return b""

    return JobDeps(
        fetch_story=api_story.fetch_story,
        fetch_chapter_html=fetch_chapter_html,
        parse_chapter=extract_chapter,
        fetch_inline_comments=api_comments.fetch_inline_comments,
        fetch_end_comments=api_comments.fetch_end_comments,
        fetch_cover_bytes=fetch_cover_bytes,
    )


def archive_story(
    cfg: Config,
    client: RateLimitedClient,
    manifest: Manifest,
    story_id: str,
    *,
    deps: JobDeps | None = None,
) -> None:
    deps = deps or _default_deps()
    logger.info("Archiving story %s", story_id)
    story: Story = deps.fetch_story(client, story_id)

    manifest.upsert_story(story)
    manifest.upsert_parts(story)
    store.write_story_metadata(cfg.output_dir, story)
    if story.cover_url:
        cover = deps.fetch_cover_bytes(client, story.cover_url)
        if cover:
            store.write_cover(cfg.output_dir, story, cover)

    for part in story.parts:
        existing = manifest.get_part(story.story_id, part.part_id)
        if existing and existing["status"] == "done":
            continue
        manifest.set_part_status(story.story_id, part.part_id, "in_progress")
        try:
            raw_html = deps.fetch_chapter_html(client, part.url)
            content: ChapterContent = deps.parse_chapter(raw_html)
            inline = deps.fetch_inline_comments(client, part.part_id)
            end = deps.fetch_end_comments(client, part.part_id)
            store.write_part_files(
                cfg.output_dir, story, part, content, raw_html, inline, end,
            )
            body_hash = hashlib.sha256(content.text.encode("utf-8")).hexdigest()
            manifest.set_part_status(
                story.story_id, part.part_id, "done", body_hash=body_hash,
            )
        except Exception as e:
            logger.exception("part %s failed: %s", part.part_id, e)
            manifest.set_part_status(
                story.story_id, part.part_id, "failed", last_error=str(e),
            )

    sd = store.story_dir(cfg.output_dir, story)
    try:
        render_txt.render_txt(sd)
        render_html.render_html(sd)
        render_epub.render_epub(sd)
    except Exception as e:
        logger.exception("render failed for %s: %s", story.story_id, e)
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/jobs.py tests/unit/test_jobs.py
git commit -m "feat(jobs): single-story archive orchestrator"
```

---

### Task 20: URL/username resolver + multi-story job

**Files:**
- Modify: `local_story_archive/jobs.py`
- Modify: `tests/unit/test_jobs.py`

- [ ] **Step 1: Add failing tests**

```python
import pytest
from local_story_archive.jobs import resolve_story_id, ResolveError


def test_resolve_numeric_id():
    assert resolve_story_id("123456789") == "123456789"


def test_resolve_story_url():
    assert resolve_story_id("https://www.wattpad.com/story/123456-some-title") == "123456"


def test_resolve_part_url_to_story_requires_lookup():
    # Part URLs need an API call; this fn just rejects them
    with pytest.raises(ResolveError):
        resolve_story_id("https://www.wattpad.com/1001-chapter-one")
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append to `local_story_archive/jobs.py`**

```python
import re


class ResolveError(Exception):
    pass


_STORY_URL_RE = re.compile(r"wattpad\.com/story/(\d+)")
_NUMERIC_RE = re.compile(r"^\d+$")


def resolve_story_id(target: str) -> str:
    target = target.strip()
    if _NUMERIC_RE.match(target):
        return target
    m = _STORY_URL_RE.search(target)
    if m:
        return m.group(1)
    raise ResolveError(
        f"Cannot resolve {target!r} to a story ID. "
        "Pass a numeric ID or a https://www.wattpad.com/story/<id>-... URL."
    )


def archive_many(
    cfg: Config,
    client: RateLimitedClient,
    manifest: Manifest,
    story_ids: list[str],
    *,
    deps: JobDeps | None = None,
) -> dict[str, str]:
    """Archive a list of stories sequentially. Returns {story_id: status}."""
    results: dict[str, str] = {}
    for sid in story_ids:
        try:
            archive_story(cfg, client, manifest, sid, deps=deps)
            results[sid] = "done"
        except Exception as e:
            logger.exception("story %s failed: %s", sid, e)
            results[sid] = f"failed: {e}"
    return results
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/jobs.py tests/unit/test_jobs.py
git commit -m "feat(jobs): URL → story_id resolver + batch runner"
```

---

## Phase 8 — CLI

### Task 21: CLI skeleton with subcommands

**Files:**
- Create: `local_story_archive/cli.py`
- Create: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from local_story_archive.cli import build_parser


def test_parser_has_expected_subcommands():
    parser = build_parser()
    args = parser.parse_args(["story", "123"])
    assert args.cmd == "story"
    assert args.target == "123"


def test_parser_library_command():
    parser = build_parser()
    args = parser.parse_args(["library", "--user", "alice"])
    assert args.cmd == "library"
    assert args.user == "alice"


def test_parser_status_command():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.cmd == "status"


def test_parser_requires_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement skeleton in `local_story_archive/cli.py`**

```python
import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local-story-archive",
        description="Archive Wattpad stories locally.",
    )
    p.add_argument(
        "--output", type=Path, default=Path("./wattpad-archive"),
        help="Local archive directory (default: ./wattpad-archive)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_story = sub.add_parser("story", help="Archive a single story")
    sp_story.add_argument("target", help="Story ID or URL")

    sp_lib = sub.add_parser("library", help="Archive your reading library")
    sp_lib.add_argument("--user", required=True, help="Your Wattpad username")

    sp_list = sub.add_parser("list", help="Archive a reading list")
    sp_list.add_argument("list_id", help="Reading list ID or URL")

    sp_url = sub.add_parser("url", help="Archive whatever a Wattpad URL points to")
    sp_url.add_argument("target", help="Any Wattpad URL")

    sub.add_parser("status", help="Show archive status")

    return p
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): argparse skeleton with subcommands"
```

---

### Task 22: Wire `story` and `url` commands

**Files:**
- Modify: `local_story_archive/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Add failing test (mocks the job runner; doesn't hit network)**

```python
from unittest.mock import patch
from local_story_archive.cli import main


def test_main_story_calls_archive_story(output_dir, monkeypatch):
    captured = {}
    def fake_archive_story(cfg, client, manifest, sid, deps=None):
        captured["sid"] = sid
        captured["out"] = cfg.output_dir
    monkeypatch.setattr("local_story_archive.cli.archive_story", fake_archive_story)
    rc = main(["--output", str(output_dir), "story", "123456"])
    assert rc == 0
    assert captured["sid"] == "123456"
    assert captured["out"] == output_dir
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Replace body of `local_story_archive/cli.py`** (keep `build_parser`, add `main`)

```python
import argparse
import logging
import sys
from pathlib import Path

from local_story_archive.config import load_config
from local_story_archive.client import RateLimitedClient
from local_story_archive.archive.state import Manifest
from local_story_archive.jobs import archive_story, archive_many, resolve_story_id


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local-story-archive",
        description="Archive Wattpad stories locally.",
    )
    p.add_argument("--output", type=Path, default=Path("./wattpad-archive"))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_story = sub.add_parser("story", help="Archive a single story")
    sp_story.add_argument("target")

    sp_lib = sub.add_parser("library", help="Archive your reading library")
    sp_lib.add_argument("--user", required=True)

    sp_list = sub.add_parser("list", help="Archive a reading list")
    sp_list.add_argument("list_id")

    sp_url = sub.add_parser("url", help="Archive whatever a Wattpad URL points to")
    sp_url.add_argument("target")

    sub.add_parser("status", help="Show archive status")
    return p


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.output)
    client = RateLimitedClient(cfg)
    manifest = Manifest(cfg.output_dir).connect()
    try:
        if args.cmd == "story":
            sid = resolve_story_id(args.target)
            archive_story(cfg, client, manifest, sid)
        elif args.cmd == "url":
            sid = resolve_story_id(args.target)
            archive_story(cfg, client, manifest, sid)
        elif args.cmd == "library":
            from local_story_archive.api.user import fetch_library
            ids = fetch_library(client, args.user)
            archive_many(cfg, client, manifest, ids)
        elif args.cmd == "list":
            from local_story_archive.api.user import fetch_list_story_ids
            ids = fetch_list_story_ids(client, args.list_id)
            archive_many(cfg, client, manifest, ids)
        elif args.cmd == "status":
            _print_status(manifest)
        return 0
    finally:
        manifest.close()
        client.close()


def _print_status(manifest: Manifest) -> None:
    cur = manifest.conn.execute(
        "SELECT status, COUNT(*) FROM stories GROUP BY status ORDER BY status"
    ).fetchall()
    print("Stories:")
    for status, n in cur:
        print(f"  {status:12s} {n:>5}")
    cur = manifest.conn.execute(
        "SELECT status, COUNT(*) FROM parts GROUP BY status ORDER BY status"
    ).fetchall()
    print("Parts:")
    for status, n in cur:
        print(f"  {status:12s} {n:>5}")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — should pass.**

- [ ] **Step 5: Commit**

```bash
git add local_story_archive/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): wire subcommands to job runner"
```

---

## Phase 9 — Integration & Polish

### Task 23: End-to-end integration test with vcrpy cassette

**Files:**
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/integration/test_end_to_end.py`
- Manually: record a cassette (one-time, not part of CI)

- [ ] **Step 1: Write the test (uses recorded cassette)**

```python
import pytest
from pathlib import Path
from local_story_archive.config import Config
from local_story_archive.client import RateLimitedClient
from local_story_archive.archive.state import Manifest
from local_story_archive.jobs import archive_story


@pytest.mark.vcr(cassette_library_dir="tests/integration/cassettes")
def test_archive_one_real_story_from_cassette(output_dir: Path):
    cfg = Config(output_dir=output_dir, rate_limit_per_sec=1000.0)
    client = RateLimitedClient(cfg)
    manifest = Manifest(output_dir).connect()
    try:
        # The cassette is for a known small public story; replace ID once recorded.
        archive_story(cfg, client, manifest, "REPLACE_ME_AFTER_RECORDING")
    finally:
        manifest.close()
        client.close()

    # Generic structural assertions:
    stories = list((output_dir / "stories").glob("*/*"))
    assert len(stories) == 1
    sd = stories[0]
    assert (sd / "metadata.json").exists()
    assert any((sd / "parts").glob("*.txt"))
    assert any((sd / "output").glob("*.epub"))
```

- [ ] **Step 2: Add cassette recording instructions in a comment** at top of test file:

```python
# To record this cassette (one-time):
#   1. Pick a tiny public Wattpad story (1–2 chapters).
#   2. Replace REPLACE_ME_AFTER_RECORDING with that story's ID.
#   3. Run:  pytest tests/integration/test_end_to_end.py --record-mode=once
#   4. Review tests/integration/cassettes/*.yaml — confirm no Cookie/auth headers.
#   5. Commit the cassette.
```

- [ ] **Step 3: Run** — test will fail until cassette is recorded. That's fine; this is a manual one-time setup step. Skip recording for now and mark the test as expected-to-skip:

Add at the top of the test file:

```python
pytestmark = pytest.mark.skip(reason="Cassette not yet recorded; record per instructions in this file.")
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_end_to_end.py
git commit -m "test(integration): vcrpy end-to-end skeleton (cassette pending)"
```

---

### Task 24: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Local Story Archive

Archive Wattpad stories — chapters, inline images, and all comments — to a local
append-only folder before they get removed.

## Install

```bash
git clone <this-repo>
cd "Local Story Archive"
python -m venv .venv
.venv/Scripts/activate     # Windows; on Linux/Mac use: source .venv/bin/activate
pip install -e .
```

## Setup (one time)

1. Run the tool once to create a default config:
   ```bash
   local-story-archive --output ./wattpad-archive status
   ```
2. Open `./wattpad-archive/_config.toml` in a text editor.
3. Get your Wattpad session cookie:
   - Log in to Wattpad in your browser
   - Open DevTools → Application/Storage → Cookies → `https://www.wattpad.com`
   - Copy the value of the `token` cookie
4. Paste it into `_config.toml` as `cookie = "..."`.
5. (Optional) Adjust `rate_limit_per_sec` (default 2.0) if you want to be politer.

## Usage

```bash
# Archive everything in your library
local-story-archive library --user yourusername

# Archive a reading list
local-story-archive list <list-id-or-url>

# Archive one story
local-story-archive story 123456789
local-story-archive url https://www.wattpad.com/story/123456-some-title

# Show status
local-story-archive status
```

## Output

```
wattpad-archive/
├── _state.sqlite           # manifest (cache; reconstructable from files)
├── _config.toml            # your settings
└── stories/<author>/<id>_<slug>/
    ├── metadata.json
    ├── cover.jpg
    ├── parts/
    │   ├── 01_<part-id>_<slug>.json    # canonical chapter data
    │   ├── 01_<part-id>_<slug>.html    # original Wattpad HTML
    │   ├── 01_<part-id>_<slug>.txt     # plain text
    │   ├── 01_<part-id>_comments-inline.json
    │   └── 01_<part-id>_comments-end.json
    └── output/
        ├── <slug>.epub
        └── <slug>.html
```

The local archive is **append-only**. The tool never deletes files, even if the
remote story is removed.

## Re-running

By default, re-running is incremental: only new or changed chapters are fetched.
The full library can be re-run safely; already-downloaded parts are skipped.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with install + usage"
```

---

### Task 25: Final test sweep + lint

- [ ] **Step 1: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass except the integration test (skipped pending cassette).

- [ ] **Step 2: Run ruff lint**

```bash
ruff check local_story_archive tests
```

Expected: no errors. Fix anything that's reported.

- [ ] **Step 3: Verify the CLI runs end-to-end against the help screen**

```bash
local-story-archive --help
local-story-archive story --help
```

Expected: help text printed for each.

- [ ] **Step 4: Final commit if anything was fixed**

```bash
git add -u
git commit -m "chore: final lint + test sweep"
```

---

## Done

At this point:
- Library/list/story/url archiving works end-to-end via the CLI.
- All artifacts (json, html, txt, comments, EPUB, standalone HTML) are produced.
- Resume is incremental and safe; the local archive is append-only.
- Manifest tracks per-part status; failures don't poison the run.

**Plan 2 (Web UI)** wraps this same `jobs.py` with a FastAPI server, HTMX templates, and an SSE progress stream. Nothing in this plan needs to change for Plan 2.
