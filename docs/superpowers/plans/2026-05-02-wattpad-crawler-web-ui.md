# Wattpad Crawler — Web UI Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local web UI on top of the existing Plan 1 core/CLI so a non-technical user can paste their cookie, click a button, watch live progress, browse downloaded stories, and read them in a browser — without ever touching a terminal beyond `wattpad-crawler serve`.

**Architecture:** A small FastAPI app sits inside `wattpad_crawler/web/`, sharing the same `jobs.py` orchestrator with the CLI. A new `JobRunner` runs `archive_story` / `archive_many` in a background thread and emits progress events through an in-memory event bus. The web UI subscribes via Server-Sent Events. Jinja2 templates + HTMX + a single CSS file — no build step, no React.

**Tech Stack:** FastAPI, uvicorn, Jinja2, HTMX (loaded from CDN), Starlette SSE, anyio (already a FastAPI dep). All Plan 1 modules consumed unchanged via `wattpad_crawler.jobs`.

**Scope of this plan:** Only the web layer + the small `jobs.py` extension to emit progress events. No changes to api/, archive/, render/, scrape/. Full backwards compatibility — the CLI continues to work identically.

---

## File Structure

```
wattpad_crawler/
├── jobs.py                         # MODIFIED: optional progress callback
├── web/
│   ├── __init__.py
│   ├── app.py                      # FastAPI app factory + lifespan
│   ├── runner.py                   # JobRunner + Job + ProgressEvent + JobManager
│   ├── routes.py                   # All HTTP routes
│   ├── library_browser.py          # Reads local archive folder for /library and /read
│   ├── templates/
│   │   ├── base.html               # shared layout
│   │   ├── setup.html              # cookie/config form
│   │   ├── dashboard.html          # "what to archive" + recent jobs
│   │   ├── job.html                # live progress page (SSE)
│   │   ├── library.html            # grid of downloaded story covers
│   │   └── reader.html             # read a chapter from local archive
│   └── static/
│       └── style.css               # single stylesheet, ~200 lines
├── cli.py                          # MODIFIED: add `serve` subcommand
└── ...

tests/
├── unit/
│   ├── test_runner.py              # JobRunner unit tests (no FastAPI)
│   ├── test_web_routes.py          # FastAPI TestClient against fake JobManager
│   └── test_library_browser.py     # filesystem-only tests, no network
└── ...
```

---

## Phase 0 — Bootstrap

### Task 1: Add web dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update `pyproject.toml`** — add to `dependencies`:

```toml
dependencies = [
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "ebooklib>=0.18",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "jinja2>=3.1",
  "sse-starlette>=2.0",
]
```

- [ ] **Step 2: Reinstall**

```bash
cd "D:/Dev/Wattpad Crawler"
pip install -e ".[dev]"
```

Expected: dependencies install without errors.

- [ ] **Step 3: Verify imports**

```bash
python -c "import fastapi, uvicorn, jinja2, sse_starlette; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add fastapi/uvicorn/jinja2/sse-starlette for web UI"
```

---

### Task 2: Web package skeleton

**Files:**
- Create: `wattpad_crawler/web/__init__.py` (empty)
- Create: `wattpad_crawler/web/templates/.gitkeep` (placeholder so git tracks the dir)
- Create: `wattpad_crawler/web/static/.gitkeep`

- [ ] **Step 1: Create `wattpad_crawler/web/__init__.py`** — empty file.

- [ ] **Step 2: Create `wattpad_crawler/web/templates/.gitkeep`** — empty file.

- [ ] **Step 3: Create `wattpad_crawler/web/static/.gitkeep`** — empty file.

- [ ] **Step 4: Update `pyproject.toml`** to ensure templates and static files are packaged:

Add at the end of the `[project]` table area (or in a new section):

```toml
[tool.hatch.build.targets.wheel]
packages = ["wattpad_crawler"]

[tool.hatch.build.targets.wheel.force-include]
"wattpad_crawler/web/templates" = "wattpad_crawler/web/templates"
"wattpad_crawler/web/static" = "wattpad_crawler/web/static"
```

- [ ] **Step 5: Reinstall and verify the package layout**

```bash
pip install -e .
python -c "from wattpad_crawler import web; print(web.__file__)"
```

Expected: prints the path to `wattpad_crawler/web/__init__.py`.

- [ ] **Step 6: Commit**

```bash
git add wattpad_crawler/web/__init__.py wattpad_crawler/web/templates/.gitkeep wattpad_crawler/web/static/.gitkeep pyproject.toml
git commit -m "chore: web package skeleton"
```

---

## Phase 1 — Progress Events + JobRunner

### Task 3: ProgressEvent + thread-safe Job event log

**Files:**
- Create: `wattpad_crawler/web/runner.py`
- Create: `tests/unit/test_runner.py`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_runner.py`

```python
import threading
import time

import pytest

from wattpad_crawler.web.runner import Job, JobStatus, ProgressEvent


def test_progress_event_holds_fields():
    e = ProgressEvent(kind="part.done", data={"part_id": "100"})
    assert e.kind == "part.done"
    assert e.data == {"part_id": "100"}
    assert e.timestamp > 0


def test_job_default_state():
    job = Job(job_id="j1", kind="archive_story", args={"story_id": "42"})
    assert job.status == JobStatus.pending
    assert job.events == []
    assert job.error is None


def test_job_emit_appends_event():
    job = Job(job_id="j1", kind="archive_story", args={})
    job.emit("part.start", {"part_id": "100"})
    assert len(job.events) == 1
    assert job.events[0].kind == "part.start"


def test_job_set_running_set_done():
    job = Job(job_id="j1", kind="archive_story", args={})
    job.set_running()
    assert job.status == JobStatus.running
    job.set_done()
    assert job.status == JobStatus.done


def test_job_set_failed_records_error():
    job = Job(job_id="j1", kind="archive_story", args={})
    job.set_failed("something exploded")
    assert job.status == JobStatus.failed
    assert job.error == "something exploded"


def test_job_emit_is_thread_safe():
    """Concurrent emits from multiple threads must not lose events."""
    job = Job(job_id="j1", kind="archive_story", args={})

    def worker(n: int):
        for i in range(50):
            job.emit("tick", {"n": n, "i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert len(job.events) == 200
    # All events accounted for
    seen = {(e.data["n"], e.data["i"]) for e in job.events}
    assert len(seen) == 200
```

- [ ] **Step 2: Run — should fail.**

```bash
pytest tests/unit/test_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `wattpad_crawler/web/runner.py`**

```python
import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass
class Job:
    job_id: str
    kind: str                       # "archive_story" | "archive_many" | "archive_library" | "archive_list"
    args: dict[str, Any]
    status: JobStatus = JobStatus.pending
    events: list[ProgressEvent] = field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append(ProgressEvent(kind=kind, data=data or {}))
            self._wake.set()

    def set_running(self) -> None:
        with self._lock:
            self.status = JobStatus.running
            self.started_at = time.time()
            self._wake.set()

    def set_done(self) -> None:
        with self._lock:
            self.status = JobStatus.done
            self.ended_at = time.time()
            self._wake.set()

    def set_failed(self, error: str) -> None:
        with self._lock:
            self.status = JobStatus.failed
            self.error = error
            self.ended_at = time.time()
            self._wake.set()

    def snapshot_events(self, after_index: int = 0) -> list[ProgressEvent]:
        """Return events after the given index (atomic snapshot)."""
        with self._lock:
            return list(self.events[after_index:])


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]
```

- [ ] **Step 4: Run tests — should pass.**

```bash
pytest tests/unit/test_runner.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add wattpad_crawler/web/runner.py tests/unit/test_runner.py
git commit -m "feat(web): Job + ProgressEvent + thread-safe event log"
```

---

### Task 4: JobManager registry

**Files:**
- Modify: `wattpad_crawler/web/runner.py` (append)
- Modify: `tests/unit/test_runner.py` (append)

- [ ] **Step 1: Add failing tests**

```python
from wattpad_crawler.web.runner import JobManager


def test_jobmanager_create_returns_job_with_unique_id():
    mgr = JobManager()
    j1 = mgr.create("archive_story", {"story_id": "1"})
    j2 = mgr.create("archive_story", {"story_id": "2"})
    assert j1.job_id != j2.job_id
    assert mgr.get(j1.job_id) is j1


def test_jobmanager_get_unknown_returns_none():
    mgr = JobManager()
    assert mgr.get("nope") is None


def test_jobmanager_list_returns_recent_first():
    mgr = JobManager()
    j1 = mgr.create("archive_story", {})
    j2 = mgr.create("archive_story", {})
    j3 = mgr.create("archive_story", {})
    listed = mgr.list_jobs()
    assert [j.job_id for j in listed] == [j3.job_id, j2.job_id, j1.job_id]
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append to `wattpad_crawler/web/runner.py`**

```python
class JobManager:
    """In-memory registry of jobs. Thread-safe."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # insertion order, oldest first
        self._lock = threading.Lock()

    def create(self, kind: str, args: dict[str, Any]) -> Job:
        job = Job(job_id=new_job_id(), kind=kind, args=args)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        """Return jobs newest-first."""
        with self._lock:
            return [self._jobs[jid] for jid in reversed(self._order)]
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add wattpad_crawler/web/runner.py tests/unit/test_runner.py
git commit -m "feat(web): JobManager registry"
```

---

### Task 5: Add progress callback to `jobs.archive_story`

**Files:**
- Modify: `wattpad_crawler/jobs.py`
- Modify: `tests/unit/test_jobs.py`

- [ ] **Step 1: Add failing tests** to `tests/unit/test_jobs.py`

```python
def test_archive_story_emits_progress_events(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    events: list[tuple[str, dict]] = []

    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps,
        progress=lambda kind, data: events.append((kind, data)),
    )

    kinds = [k for k, _ in events]
    assert "story.start" in kinds
    assert "part.start" in kinds
    assert "part.done" in kinds
    assert "story.done" in kinds
    # part.start data carries identifying info
    part_start = next(d for k, d in events if k == "part.start")
    assert part_start["part_id"] == "100"
    assert part_start["ordinal"] == 1
    manifest.close()


def test_archive_story_emits_part_failed_on_error(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)
    deps.fetch_chapter_html.side_effect = RuntimeError("bad")
    events: list[tuple[str, dict]] = []

    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps,
        progress=lambda kind, data: events.append((kind, data)),
    )

    kinds = [k for k, _ in events]
    assert "part.failed" in kinds
    failed = next(d for k, d in events if k == "part.failed")
    assert "bad" in failed["error"]
    manifest.close()


def test_archive_story_progress_default_is_noop(output_dir: Path):
    """Calling without a progress callback must still work (CLI path)."""
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(story_id="42", title="Hi", author_username="bob",
                  parts=[Part(part_id="100", ordinal=1, title="One", url="https://w")])
    fake_client = MagicMock()
    deps = _make_deps(story)
    archive_story(cfg, fake_client, manifest, "42", deps=deps)  # no progress=
    manifest.close()
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Update `wattpad_crawler/jobs.py`**

Modify `archive_story` to accept and call `progress`. Replace its body with:

```python
ProgressCallback = Callable[[str, dict], None]


def _noop_progress(_kind: str, _data: dict) -> None:
    pass


def archive_story(
    cfg: Config,
    client: RateLimitedClient,
    manifest: Manifest,
    story_id: str,
    *,
    deps: JobDeps | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    deps = deps or _default_deps()
    emit = progress or _noop_progress
    logger.info("Archiving story %s", story_id)
    emit("story.fetch", {"story_id": story_id})
    story: Story = deps.fetch_story(client, story_id)
    emit("story.start", {
        "story_id": story.story_id,
        "title": story.title,
        "author": story.author_username,
        "parts_total": len(story.parts),
    })

    manifest.upsert_story(story)
    manifest.upsert_parts(story)
    store.write_story_metadata(cfg.output_dir, story)
    if story.cover_url:
        try:
            cover = deps.fetch_cover_bytes(client, story.cover_url)
            if cover:
                store.write_cover(cfg.output_dir, story, cover)
        except Exception as e:
            logger.warning("cover fetch failed for %s: %s", story.story_id, e)

    for part in story.parts:
        existing = manifest.get_part(story.story_id, part.part_id)
        if existing and existing["status"] == "done":
            emit("part.skipped", {"part_id": part.part_id, "ordinal": part.ordinal})
            continue
        emit("part.start", {
            "part_id": part.part_id,
            "ordinal": part.ordinal,
            "title": part.title,
        })
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
            emit("part.done", {
                "part_id": part.part_id,
                "ordinal": part.ordinal,
                "inline_comments": len(inline),
                "end_comments": len(end),
            })
        except Exception as e:
            logger.exception("part %s failed: %s", part.part_id, e)
            manifest.set_part_status(
                story.story_id, part.part_id, "failed", last_error=str(e),
            )
            emit("part.failed", {"part_id": part.part_id, "error": str(e)})

    sd = store.story_dir(cfg.output_dir, story)
    emit("render.start", {"story_id": story.story_id})
    for name, fn in (
        ("txt", render_txt.render_txt),
        ("html", render_html.render_html),
        ("epub", render_epub.render_epub),
    ):
        try:
            fn(sd)
        except Exception as e:
            logger.exception("render(%s) failed for %s: %s", name, story.story_id, e)
            emit("render.failed", {"format": name, "error": str(e)})
    emit("story.done", {"story_id": story.story_id})
```

- [ ] **Step 4: Update `archive_many` to pass progress through**

```python
def archive_many(
    cfg: Config,
    client: RateLimitedClient,
    manifest: Manifest,
    story_ids: list[str],
    *,
    deps: JobDeps | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, str]:
    """Archive a list of stories sequentially. Returns {story_id: status}."""
    emit = progress or _noop_progress
    results: dict[str, str] = {}
    emit("batch.start", {"total": len(story_ids), "story_ids": list(story_ids)})
    for i, sid in enumerate(story_ids):
        emit("batch.story", {"index": i, "total": len(story_ids), "story_id": sid})
        try:
            archive_story(cfg, client, manifest, sid, deps=deps, progress=progress)
            results[sid] = "done"
        except Exception as e:
            logger.exception("story %s failed: %s", sid, e)
            results[sid] = f"failed: {e}"
            emit("batch.failed", {"story_id": sid, "error": str(e)})
    emit("batch.done", {"results": results})
    return results
```

- [ ] **Step 5: Run tests — should pass.**

```bash
pytest tests/unit/test_jobs.py -v
```

Expected: 8 passed (5 prior + 3 new).

- [ ] **Step 6: Run full suite to confirm CLI still works**

```bash
pytest tests/unit -v
```

Expected: 121 passed (118 + 3 new).

- [ ] **Step 7: Commit**

```bash
git add wattpad_crawler/jobs.py tests/unit/test_jobs.py
git commit -m "feat(jobs): optional progress callback, default no-op for CLI"
```

---

### Task 6: JobRunner — runs jobs in background thread

**Files:**
- Modify: `wattpad_crawler/web/runner.py`
- Modify: `tests/unit/test_runner.py`

- [ ] **Step 1: Add failing tests**

```python
import time
from pathlib import Path
from unittest.mock import MagicMock

from wattpad_crawler.web.runner import JobManager, JobRunner, JobStatus


def test_jobrunner_runs_callable_in_thread_and_marks_done():
    mgr = JobManager()
    job = mgr.create("test", {})
    runner = JobRunner(mgr)

    def work(emit):
        emit("test.tick", {"n": 1})
        emit("test.tick", {"n": 2})

    runner.submit(job, work)
    # Wait for completion (with timeout)
    deadline = time.monotonic() + 2.0
    while job.status not in (JobStatus.done, JobStatus.failed):
        if time.monotonic() > deadline:
            raise AssertionError("job did not complete")
        time.sleep(0.01)
    assert job.status == JobStatus.done
    assert len(job.events) == 2


def test_jobrunner_records_failure():
    mgr = JobManager()
    job = mgr.create("test", {})
    runner = JobRunner(mgr)

    def boom(emit):
        emit("started", {})
        raise RuntimeError("kaboom")

    runner.submit(job, boom)
    deadline = time.monotonic() + 2.0
    while job.status not in (JobStatus.done, JobStatus.failed):
        if time.monotonic() > deadline:
            raise AssertionError("job did not finish")
        time.sleep(0.01)
    assert job.status == JobStatus.failed
    assert "kaboom" in job.error


def test_jobrunner_running_jobs_count():
    mgr = JobManager()
    runner = JobRunner(mgr)
    started = threading.Event()
    done = threading.Event()

    def slow(emit):
        started.set()
        done.wait(timeout=2)

    job = mgr.create("test", {})
    runner.submit(job, slow)
    started.wait(timeout=1)
    assert runner.running_count() >= 1
    done.set()
    deadline = time.monotonic() + 2.0
    while job.status != JobStatus.done:
        if time.monotonic() > deadline:
            raise AssertionError("job did not complete")
        time.sleep(0.01)
    assert runner.running_count() == 0
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append to `wattpad_crawler/web/runner.py`**

```python
import logging
from typing import Callable

logger = logging.getLogger(__name__)


JobWork = Callable[[Callable[[str, dict], None]], None]
"""A unit of work for JobRunner.submit. Receives an `emit(kind, data)` callable."""


class JobRunner:
    """Runs Job functions in background threads. One thread per job."""

    def __init__(self, manager: JobManager) -> None:
        self.manager = manager
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job: Job, work: JobWork) -> None:
        thread = threading.Thread(
            target=self._run, args=(job, work), name=f"job-{job.job_id}", daemon=True
        )
        with self._lock:
            self._running.add(job.job_id)
        thread.start()

    def _run(self, job: Job, work: JobWork) -> None:
        try:
            job.set_running()
            work(job.emit)
            job.set_done()
        except Exception as e:
            logger.exception("Job %s failed", job.job_id)
            job.set_failed(str(e))
        finally:
            with self._lock:
                self._running.discard(job.job_id)

    def running_count(self) -> int:
        with self._lock:
            return len(self._running)
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add wattpad_crawler/web/runner.py tests/unit/test_runner.py
git commit -m "feat(web): JobRunner runs work in background threads"
```

---

## Phase 2 — FastAPI App + Templates

### Task 7: FastAPI app factory + base template + style.css

**Files:**
- Create: `wattpad_crawler/web/app.py`
- Create: `wattpad_crawler/web/templates/base.html`
- Create: `wattpad_crawler/web/static/style.css`
- Create: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Write failing test** — `tests/unit/test_web_routes.py`

```python
from pathlib import Path

from fastapi.testclient import TestClient

from wattpad_crawler.config import Config
from wattpad_crawler.web.app import build_app


def test_app_health_endpoint(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/_health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_static_css_served(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Create `wattpad_crawler/web/static/style.css`**

```css
:root {
  --bg: #fafaf8;
  --fg: #1a1a1a;
  --muted: #666;
  --border: #d8d8d4;
  --accent: #b91c1c;
  --accent-fg: #fff;
  --max-width: 64em;
  --reading-width: 42em;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, -apple-system, sans-serif;
  line-height: 1.5;
}
.container {
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 2em 1em;
}
nav.topbar {
  background: #fff;
  border-bottom: 1px solid var(--border);
  padding: 0.75em 1em;
}
nav.topbar a {
  color: var(--fg);
  text-decoration: none;
  margin-right: 1.5em;
  font-weight: 500;
}
nav.topbar a:hover { color: var(--accent); }
h1, h2, h3 { font-family: Georgia, serif; }
button, .btn {
  background: var(--accent);
  color: var(--accent-fg);
  border: none;
  padding: 0.6em 1.2em;
  font-size: 1em;
  border-radius: 4px;
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
}
button:hover, .btn:hover { background: #991b1b; }
input[type=text], input[type=password], textarea {
  width: 100%;
  padding: 0.5em;
  border: 1px solid var(--border);
  border-radius: 4px;
  font: inherit;
}
.card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.5em;
  margin-bottom: 1em;
}
.three-buttons { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1em; }
.three-buttons .card { text-align: center; }
.muted { color: var(--muted); }
.tag {
  display: inline-block;
  padding: 0.1em 0.5em;
  background: #eee;
  border-radius: 3px;
  font-size: 0.8em;
  margin-right: 0.3em;
}
.status-pending { color: var(--muted); }
.status-running { color: #2563eb; }
.status-done { color: #15803d; }
.status-failed { color: var(--accent); }
.event-log {
  font-family: ui-monospace, monospace;
  font-size: 0.85em;
  background: #f4f4f0;
  padding: 1em;
  border-radius: 4px;
  max-height: 30em;
  overflow-y: auto;
}
.event-log .ev { margin-bottom: 0.2em; }
.library-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 1em;
}
.library-item {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 4px;
  text-align: center;
  text-decoration: none;
  color: var(--fg);
  overflow: hidden;
}
.library-item img { width: 100%; aspect-ratio: 2/3; object-fit: cover; }
.library-item .title {
  padding: 0.5em;
  font-size: 0.9em;
  font-weight: 500;
}
.library-item .author { padding: 0 0.5em 0.5em; font-size: 0.8em; color: var(--muted); }
.reader { max-width: var(--reading-width); margin: 0 auto; }
.reader .chapter-body { font-family: Georgia, serif; font-size: 1.1em; }
.reader .chapter-body pre { white-space: pre-wrap; font-family: inherit; margin: 1em 0; }
```

- [ ] **Step 4: Create `wattpad_crawler/web/templates/base.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Wattpad Crawler{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://unpkg.com/htmx.org@1.9.10" defer></script>
</head>
<body>
  <nav class="topbar">
    <a href="/">Dashboard</a>
    <a href="/library">Library</a>
    <a href="/setup">Setup</a>
  </nav>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 5: Create `wattpad_crawler/web/app.py`**

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wattpad_crawler.config import Config

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"


def build_app(cfg: Config) -> FastAPI:
    """Construct the FastAPI app. cfg is stashed on app.state for routes to use."""
    app = FastAPI(title="Wattpad Crawler", docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/_health")
    def health() -> dict:
        return {"status": "ok"}

    return app
```

- [ ] **Step 6: Run tests — should pass.**

```bash
pytest tests/unit/test_web_routes.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add wattpad_crawler/web/app.py wattpad_crawler/web/templates/base.html wattpad_crawler/web/static/style.css tests/unit/test_web_routes.py
git commit -m "feat(web): FastAPI app factory + base template + stylesheet"
```

---

## Phase 3 — Setup Screen

### Task 8: Setup form (GET + POST)

**Files:**
- Create: `wattpad_crawler/web/routes.py`
- Modify: `wattpad_crawler/web/app.py`
- Create: `wattpad_crawler/web/templates/setup.html`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing tests** to `tests/unit/test_web_routes.py`

```python
def test_setup_page_renders(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/setup")
    assert r.status_code == 200
    assert "cookie" in r.text.lower()
    assert "wattpad" in r.text.lower()


def test_setup_post_saves_cookie(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/setup", data={"cookie": "tok-abc-123"})
    assert r.status_code in (200, 303)  # redirect or render
    # Cookie was written to _config.toml
    text = (output_dir / "_config.toml").read_text()
    assert "tok-abc-123" in text


def test_setup_post_strips_whitespace(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    client.post("/setup", data={"cookie": "  tok-abc-123  \n"})
    text = (output_dir / "_config.toml").read_text()
    assert 'cookie = "tok-abc-123"' in text
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Create `wattpad_crawler/web/templates/setup.html`**

```html
{% extends "base.html" %}
{% block title %}Setup — Wattpad Crawler{% endblock %}
{% block content %}
  <h1>Setup</h1>
  <p>Paste your Wattpad session cookie here. The tool uses it to access your library and any login-required stories.</p>

  <ol>
    <li>Log in to <a href="https://www.wattpad.com" target="_blank">Wattpad</a> in your browser.</li>
    <li>Open DevTools (F12) → Application/Storage → Cookies → <code>https://www.wattpad.com</code>.</li>
    <li>Copy the value of the <code>token</code> cookie and paste it below.</li>
  </ol>

  <form method="post" action="/setup">
    <p>
      <label for="cookie"><strong>Wattpad <code>token</code> cookie:</strong></label><br>
      <input type="password" id="cookie" name="cookie" value="{{ current_cookie_masked }}" required>
    </p>
    <p>
      <button type="submit">Save</button>
    </p>
  </form>

  {% if saved %}
    <p class="status-done">Saved! You can now <a href="/">archive a story</a>.</p>
  {% endif %}

  <p class="muted">Output directory: <code>{{ output_dir }}</code></p>
{% endblock %}
```

- [ ] **Step 4: Create `wattpad_crawler/web/routes.py`**

```python
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def _save_cookie(output_dir: Path, cookie: str) -> None:
    """Write/update the cookie line in _config.toml. Preserves other settings."""
    config_path = output_dir / "_config.toml"
    cookie = cookie.strip()
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        new_lines = []
        replaced = False
        for line in lines:
            if line.lstrip().startswith("cookie "):
                new_lines.append(f'cookie = "{cookie}"')
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f'cookie = "{cookie}"')
        config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f'cookie = "{cookie}"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
            encoding="utf-8",
        )


def _mask(s: str) -> str:
    if not s:
        return ""
    return s[:4] + "…" + s[-4:] if len(s) > 8 else "…"


@router.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "current_cookie_masked": _mask(cfg.cookie),
            "output_dir": str(cfg.output_dir),
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/setup")
def setup_post(request: Request, cookie: str = Form(...)) -> RedirectResponse:
    cfg = request.app.state.cfg
    _save_cookie(cfg.output_dir, cookie)
    # Reload Config so subsequent requests see the new cookie
    from wattpad_crawler.config import load_config
    request.app.state.cfg = load_config(cfg.output_dir)
    return RedirectResponse(url="/setup?saved=1", status_code=303)
```

- [ ] **Step 5: Wire the router into the app** — modify `wattpad_crawler/web/app.py`'s `build_app`:

Replace `build_app` body to include the router:

```python
def build_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="Wattpad Crawler", docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/_health")
    def health() -> dict:
        return {"status": "ok"}

    from wattpad_crawler.web.routes import router as main_router
    app.include_router(main_router)

    return app
```

- [ ] **Step 6: Run tests — should pass.**

- [ ] **Step 7: Commit**

```bash
git add wattpad_crawler/web/routes.py wattpad_crawler/web/app.py wattpad_crawler/web/templates/setup.html tests/unit/test_web_routes.py
git commit -m "feat(web): /setup page — paste cookie, save to _config.toml"
```

---

## Phase 4 — Dashboard + Job Submission

### Task 9: Dashboard page

**Files:**
- Modify: `wattpad_crawler/web/routes.py`
- Modify: `wattpad_crawler/web/app.py` (instantiate JobManager + JobRunner; share via app.state)
- Create: `wattpad_crawler/web/templates/dashboard.html`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing test**

```python
def test_dashboard_renders(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "library" in r.text.lower()
    assert "story" in r.text.lower()
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Update `wattpad_crawler/web/app.py` — add JobManager + JobRunner to app.state**

Replace `build_app`:

```python
def build_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="Wattpad Crawler", docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from wattpad_crawler.web.runner import JobManager, JobRunner
    app.state.job_manager = JobManager()
    app.state.job_runner = JobRunner(app.state.job_manager)

    @app.get("/_health")
    def health() -> dict:
        return {"status": "ok"}

    from wattpad_crawler.web.routes import router as main_router
    app.include_router(main_router)

    return app
```

- [ ] **Step 4: Create `wattpad_crawler/web/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Wattpad Crawler{% endblock %}
{% block content %}
  <h1>What do you want to archive?</h1>

  {% if not has_cookie %}
    <div class="card status-failed">
      <strong>No cookie configured.</strong> You need to <a href="/setup">paste your Wattpad cookie</a> first
      to archive your library or login-required stories.
    </div>
  {% endif %}

  <div class="three-buttons">
    <form method="post" action="/jobs" class="card">
      <h3>My library</h3>
      <p>Archive everything you've added on Wattpad.</p>
      <p>
        <input type="text" name="username" placeholder="your Wattpad username" required>
      </p>
      <input type="hidden" name="kind" value="library">
      <button type="submit">Archive library</button>
    </form>

    <form method="post" action="/jobs" class="card">
      <h3>One story</h3>
      <p>Archive a single story by ID or URL.</p>
      <p>
        <input type="text" name="target" placeholder="story ID or wattpad.com URL" required>
      </p>
      <input type="hidden" name="kind" value="story">
      <button type="submit">Archive story</button>
    </form>

    <form method="post" action="/jobs" class="card">
      <h3>Reading list</h3>
      <p>Archive all stories in a reading list.</p>
      <p>
        <input type="text" name="list_id" placeholder="reading list ID" required>
      </p>
      <input type="hidden" name="kind" value="list">
      <button type="submit">Archive list</button>
    </form>
  </div>

  <h2>Recent jobs</h2>
  {% if recent_jobs %}
    <ul>
      {% for job in recent_jobs %}
        <li>
          <a href="/jobs/{{ job.job_id }}">
            <span class="status-{{ job.status.value }}">[{{ job.status.value }}]</span>
            {{ job.kind }} —
            {% for k, v in job.args.items() %}<span class="muted">{{ k }}={{ v }}</span> {% endfor %}
          </a>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <p class="muted">No jobs yet.</p>
  {% endif %}
{% endblock %}
```

- [ ] **Step 5: Add the route to `wattpad_crawler/web/routes.py`**

Append:

```python
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    mgr = request.app.state.job_manager
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "has_cookie": bool(cfg.cookie),
            "recent_jobs": mgr.list_jobs()[:10],
        },
    )
```

- [ ] **Step 6: Run tests — should pass.**

- [ ] **Step 7: Commit**

```bash
git add wattpad_crawler/web/app.py wattpad_crawler/web/routes.py wattpad_crawler/web/templates/dashboard.html tests/unit/test_web_routes.py
git commit -m "feat(web): dashboard with three job-launch forms"
```

---

### Task 10: POST /jobs — submit a new archive job

**Files:**
- Modify: `wattpad_crawler/web/routes.py`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing tests**

```python
import time


def test_post_jobs_story_creates_and_starts(output_dir: Path, monkeypatch):
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    captured = {}

    def fake_archive_story(cfg_arg, _client, _manifest, sid, *, deps=None, progress=None):
        captured["sid"] = sid
        if progress:
            progress("story.start", {"story_id": sid})

    monkeypatch.setattr("wattpad_crawler.web.routes.archive_story", fake_archive_story)
    r = client.post("/jobs", data={"kind": "story", "target": "12345"})
    assert r.status_code == 303  # redirect to job page
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    # Wait for the job to be done (it's a thread)
    deadline = time.monotonic() + 2.0
    job = app.state.job_manager.get(job_id)
    while job.status.value == "pending" or job.status.value == "running":
        if time.monotonic() > deadline:
            raise AssertionError(f"job stuck at {job.status}")
        time.sleep(0.01)
    assert captured["sid"] == "12345"


def test_post_jobs_url_resolves(output_dir: Path, monkeypatch):
    cfg = Config(output_dir=output_dir, cookie="tok")
    app = build_app(cfg)
    client = TestClient(app)

    captured = {}

    def fake_archive_story(cfg_arg, _client, _manifest, sid, *, deps=None, progress=None):
        captured["sid"] = sid

    monkeypatch.setattr("wattpad_crawler.web.routes.archive_story", fake_archive_story)
    r = client.post("/jobs", data={
        "kind": "story",
        "target": "https://www.wattpad.com/story/789-foo-bar",
    })
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 2.0
    job = app.state.job_manager.get(job_id)
    while job.status.value in ("pending", "running"):
        if time.monotonic() > deadline:
            raise AssertionError("job stuck")
        time.sleep(0.01)
    assert captured["sid"] == "789"


def test_post_jobs_invalid_kind_returns_400(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.post("/jobs", data={"kind": "garbage"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Append to `wattpad_crawler/web/routes.py`**

Add at the top of the file (with other imports):

```python
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.jobs import (
    archive_many,
    archive_story,
    resolve_story_id,
    ResolveError,
)
from wattpad_crawler.api.user import fetch_library, fetch_list_story_ids
```

Add the route:

```python
def _build_work(cfg, kind: str, args: dict):
    """Build a JobWork callable that opens its own client+manifest, runs the job,
    then closes them. The returned function is what JobRunner.submit consumes."""
    def work(emit):
        client = RateLimitedClient(cfg)
        manifest = Manifest(cfg.output_dir).connect()
        try:
            if kind == "story":
                archive_story(cfg, client, manifest, args["story_id"], progress=emit)
            elif kind == "library":
                ids = fetch_library(client, args["username"])
                archive_many(cfg, client, manifest, ids, progress=emit)
            elif kind == "list":
                ids = fetch_list_story_ids(client, args["list_id"])
                archive_many(cfg, client, manifest, ids, progress=emit)
        finally:
            manifest.close()
            client.close()
    return work


@router.post("/jobs")
async def submit_job(request: Request) -> RedirectResponse:
    form = await request.form()
    kind = form.get("kind")
    cfg = request.app.state.cfg
    mgr = request.app.state.job_manager
    runner = request.app.state.job_runner

    if kind == "story":
        target = form.get("target", "").strip()
        try:
            sid = resolve_story_id(target)
        except ResolveError as e:
            raise HTTPException(status_code=400, detail=str(e))
        job = mgr.create("archive_story", {"story_id": sid, "target": target})
        runner.submit(job, _build_work(cfg, "story", {"story_id": sid}))
    elif kind == "library":
        username = form.get("username", "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="username required")
        job = mgr.create("archive_library", {"username": username})
        runner.submit(job, _build_work(cfg, "library", {"username": username}))
    elif kind == "list":
        list_id = form.get("list_id", "").strip()
        if not list_id:
            raise HTTPException(status_code=400, detail="list_id required")
        job = mgr.create("archive_list", {"list_id": list_id})
        runner.submit(job, _build_work(cfg, "list", {"list_id": list_id}))
    else:
        raise HTTPException(status_code=400, detail=f"unknown kind: {kind}")

    return RedirectResponse(url=f"/jobs/{job.job_id}", status_code=303)
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add wattpad_crawler/web/routes.py tests/unit/test_web_routes.py
git commit -m "feat(web): POST /jobs — create + start an archive job"
```

---

## Phase 5 — Job View + SSE Live Progress

### Task 11: GET /jobs/{id} — job detail page

**Files:**
- Modify: `wattpad_crawler/web/routes.py`
- Create: `wattpad_crawler/web/templates/job.html`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing test**

```python
def test_job_detail_page(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    job = app.state.job_manager.create("archive_story", {"story_id": "42"})
    job.emit("story.start", {"story_id": "42", "title": "Hi"})
    client = TestClient(app)
    r = client.get(f"/jobs/{job.job_id}")
    assert r.status_code == 200
    assert "42" in r.text
    assert "story.start" in r.text


def test_job_detail_unknown_returns_404(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/jobs/nonexistent")
    assert r.status_code == 404
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Create `wattpad_crawler/web/templates/job.html`**

```html
{% extends "base.html" %}
{% block title %}Job {{ job.job_id }} — Wattpad Crawler{% endblock %}
{% block content %}
  <h1>
    <span class="status-{{ job.status.value }}">[{{ job.status.value }}]</span>
    {{ job.kind }}
  </h1>
  <p class="muted">
    Job ID: <code>{{ job.job_id }}</code>
    {% for k, v in job.args.items() %}— {{ k }}: <code>{{ v }}</code>{% endfor %}
  </p>

  {% if job.error %}
    <div class="card status-failed">
      <strong>Error:</strong> {{ job.error }}
    </div>
  {% endif %}

  <h2>Progress</h2>
  <div class="event-log" id="event-log">
    {% for ev in job.events %}
      <div class="ev"><code>{{ ev.kind }}</code> {{ ev.data }}</div>
    {% endfor %}
  </div>

  {% if job.status.value not in ("done", "failed") %}
    <script>
      (function () {
        var log = document.getElementById('event-log');
        var es = new EventSource("/jobs/{{ job.job_id }}/stream?after={{ job.events|length }}");
        es.onmessage = function (e) {
          var data = JSON.parse(e.data);
          if (data.kind === '__status__') {
            // Job ended; reload to refresh status badge
            es.close();
            setTimeout(function () { location.reload(); }, 500);
            return;
          }
          var div = document.createElement('div');
          div.className = 'ev';
          div.innerHTML = '<code>' + data.kind + '</code> ' + JSON.stringify(data.data);
          log.appendChild(div);
          log.scrollTop = log.scrollHeight;
        };
        es.onerror = function () { es.close(); };
      })();
    </script>
  {% endif %}

  <p><a href="/">← Dashboard</a></p>
{% endblock %}
```

- [ ] **Step 4: Add the route to `wattpad_crawler/web/routes.py`**

```python
@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> HTMLResponse:
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="job.html",
        context={"job": job},
    )
```

- [ ] **Step 5: Run tests — should pass.**

- [ ] **Step 6: Commit**

```bash
git add wattpad_crawler/web/routes.py wattpad_crawler/web/templates/job.html tests/unit/test_web_routes.py
git commit -m "feat(web): /jobs/{id} detail page with event log"
```

---

### Task 12: GET /jobs/{id}/stream — Server-Sent Events for live progress

**Files:**
- Modify: `wattpad_crawler/web/routes.py`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing tests**

```python
def test_sse_stream_replays_existing_events(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    job = app.state.job_manager.create("test", {})
    job.emit("test.tick", {"n": 1})
    job.emit("test.tick", {"n": 2})
    job.set_done()
    client = TestClient(app)
    with client.stream("GET", f"/jobs/{job.job_id}/stream?after=0") as r:
        assert r.status_code == 200
        text = "".join(chunk for chunk in r.iter_text())
    assert "test.tick" in text
    assert '"n": 1' in text or '"n":1' in text


def test_sse_stream_404_unknown_job(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/jobs/missing/stream")
    assert r.status_code == 404
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Add SSE route** to `wattpad_crawler/web/routes.py`

Add at the top of the file:

```python
import json
from sse_starlette.sse import EventSourceResponse
```

Add the route:

```python
@router.get("/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: str, after: int = 0):
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        import asyncio
        index = after
        while True:
            if await request.is_disconnected():
                break
            new_events = job.snapshot_events(index)
            for ev in new_events:
                index += 1
                yield {
                    "data": json.dumps({"kind": ev.kind, "data": ev.data, "ts": ev.timestamp})
                }
            # If job is finished and we've drained, send a sentinel and stop.
            if job.status.value in ("done", "failed"):
                yield {
                    "data": json.dumps({
                        "kind": "__status__",
                        "data": {"status": job.status.value, "error": job.error},
                    })
                }
                return
            # Brief sleep before checking again. Real wakeup signal would be better
            # (Job._wake) but mixing threading.Event with asyncio is fiddly; 250ms
            # polling is fine for a personal-use UI.
            await asyncio.sleep(0.25)

    return EventSourceResponse(event_gen())
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add wattpad_crawler/web/routes.py tests/unit/test_web_routes.py
git commit -m "feat(web): SSE stream for live job progress"
```

---

## Phase 6 — Library + Reader

### Task 13: Library browser — list local archive

**Files:**
- Create: `wattpad_crawler/web/library_browser.py`
- Create: `tests/unit/test_library_browser.py`

- [ ] **Step 1: Write failing tests**

```python
import json
from pathlib import Path

from wattpad_crawler.web.library_browser import LibraryEntry, scan_library


def test_scan_library_empty(output_dir: Path):
    assert scan_library(output_dir) == []


def test_scan_library_one_story(output_dir: Path):
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42",
        "title": "My Tale",
        "author_username": "alice",
        "tags": ["fantasy"],
        "description": "d",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    out = scan_library(output_dir)
    assert len(out) == 1
    e = out[0]
    assert isinstance(e, LibraryEntry)
    assert e.story_id == "42"
    assert e.title == "My Tale"
    assert e.author == "alice"
    assert e.tags == ["fantasy"]
    assert e.parts_count == 1
    assert e.dir_name == "42_my-tale"
    assert e.has_cover is False


def test_scan_library_detects_cover(output_dir: Path):
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "cover.jpg").write_bytes(b"x")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "T", "author_username": "alice",
        "tags": [], "parts": [],
    }))
    [e] = scan_library(output_dir)
    assert e.has_cover is True


def test_scan_library_skips_dirs_without_metadata(output_dir: Path):
    """Directories that look like story dirs but have no metadata.json are skipped."""
    (output_dir / "stories" / "alice" / "42_x" / "parts").mkdir(parents=True)
    # No metadata.json
    out = scan_library(output_dir)
    assert out == []


def test_scan_library_sorts_by_author_then_title(output_dir: Path):
    for author, story_id, title in [
        ("zelda", "1", "First"),
        ("alice", "2", "Second"),
        ("alice", "3", "Aardvark"),
    ]:
        sd = output_dir / "stories" / author / f"{story_id}_x"
        sd.mkdir(parents=True)
        (sd / "metadata.json").write_text(json.dumps({
            "story_id": story_id, "title": title, "author_username": author,
            "tags": [], "parts": [],
        }))
    titles = [(e.author, e.title) for e in scan_library(output_dir)]
    assert titles == [("alice", "Aardvark"), ("alice", "Second"), ("zelda", "First")]
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Implement `wattpad_crawler/web/library_browser.py`**

```python
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LibraryEntry:
    story_id: str
    title: str
    author: str
    description: str
    tags: list[str]
    parts_count: int
    dir_name: str          # e.g. "42_my-tale" — used for URL building
    has_cover: bool
    storage_path: Path     # absolute path to the story directory


def scan_library(output_dir: Path) -> list[LibraryEntry]:
    """Walk <output>/stories/<author>/<id>_<slug>/ and return one LibraryEntry per
    story directory that contains a metadata.json. Sorted by (author, title)."""
    stories_root = output_dir / "stories"
    if not stories_root.exists():
        return []

    entries: list[LibraryEntry] = []
    for author_dir in stories_root.iterdir():
        if not author_dir.is_dir():
            continue
        for story_dir in author_dir.iterdir():
            if not story_dir.is_dir():
                continue
            meta_path = story_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append(LibraryEntry(
                story_id=str(meta.get("story_id", "")),
                title=meta.get("title", ""),
                author=meta.get("author_username", author_dir.name),
                description=meta.get("description", ""),
                tags=list(meta.get("tags", []) or []),
                parts_count=len(meta.get("parts", []) or []),
                dir_name=story_dir.name,
                has_cover=(story_dir / "cover.jpg").exists(),
                storage_path=story_dir,
            ))
    entries.sort(key=lambda e: (e.author.lower(), e.title.lower()))
    return entries
```

- [ ] **Step 4: Run tests — should pass.**

- [ ] **Step 5: Commit**

```bash
git add wattpad_crawler/web/library_browser.py tests/unit/test_library_browser.py
git commit -m "feat(web): library browser scans local archive"
```

---

### Task 14: GET /library page

**Files:**
- Modify: `wattpad_crawler/web/routes.py`
- Create: `wattpad_crawler/web/templates/library.html`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing tests**

```python
def test_library_empty(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library")
    assert r.status_code == 200
    assert "no stories" in r.text.lower() or "empty" in r.text.lower()


def test_library_lists_stories(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    sd.mkdir(parents=True)
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": ["x"], "description": "d", "parts": [],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/library")
    assert r.status_code == 200
    assert "My Tale" in r.text
    assert "alice" in r.text
```

(Add `import json` at the top of the test file if not already there.)

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Create `wattpad_crawler/web/templates/library.html`**

```html
{% extends "base.html" %}
{% block title %}Library — Wattpad Crawler{% endblock %}
{% block content %}
  <h1>Library</h1>
  {% if entries %}
    <p class="muted">{{ entries|length }} stories archived.</p>
    <div class="library-grid">
      {% for e in entries %}
        <a class="library-item" href="/read/{{ e.author }}/{{ e.dir_name }}">
          {% if e.has_cover %}
            <img src="/library/cover/{{ e.author }}/{{ e.dir_name }}" alt="cover">
          {% else %}
            <div style="aspect-ratio:2/3;background:#eee;display:flex;align-items:center;justify-content:center;color:#999;">no cover</div>
          {% endif %}
          <div class="title">{{ e.title }}</div>
          <div class="author">by {{ e.author }}</div>
        </a>
      {% endfor %}
    </div>
  {% else %}
    <p>No stories yet. <a href="/">Archive one</a> to get started — the library is empty.</p>
  {% endif %}
{% endblock %}
```

- [ ] **Step 4: Add routes** to `wattpad_crawler/web/routes.py`

```python
from fastapi.responses import FileResponse

from wattpad_crawler.web.library_browser import scan_library


@router.get("/library", response_class=HTMLResponse)
def library(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    entries = scan_library(cfg.output_dir)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="library.html",
        context={"entries": entries},
    )


@router.get("/library/cover/{author}/{dir_name}")
def library_cover(request: Request, author: str, dir_name: str) -> FileResponse:
    cfg = request.app.state.cfg
    # Resolve under stories/ root, reject path traversal
    target = (cfg.output_dir / "stories" / author / dir_name / "cover.jpg").resolve()
    stories_root = (cfg.output_dir / "stories").resolve()
    if not target.is_relative_to(stories_root) or not target.exists():
        raise HTTPException(status_code=404, detail="cover not found")
    return FileResponse(target, media_type="image/jpeg")
```

- [ ] **Step 5: Run tests — should pass.**

- [ ] **Step 6: Commit**

```bash
git add wattpad_crawler/web/routes.py wattpad_crawler/web/templates/library.html tests/unit/test_web_routes.py
git commit -m "feat(web): /library grid view + cover serving"
```

---

### Task 15: Reader — story TOC + chapter view

**Files:**
- Modify: `wattpad_crawler/web/routes.py`
- Create: `wattpad_crawler/web/templates/reader.html`
- Modify: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Add failing tests**

```python
def test_reader_story_toc(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.txt").write_text("Body of chapter one.")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/42_my-tale")
    assert r.status_code == 200
    assert "My Tale" in r.text
    assert "One" in r.text  # chapter title in TOC


def test_reader_chapter_view(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    sd = output_dir / "stories" / "alice" / "42_my-tale"
    (sd / "parts").mkdir(parents=True)
    (sd / "parts" / "01_100_one.txt").write_text("Body of chapter one.")
    (sd / "metadata.json").write_text(json.dumps({
        "story_id": "42", "title": "My Tale", "author_username": "alice",
        "tags": [], "description": "",
        "parts": [{"part_id": "100", "ordinal": 1, "title": "One"}],
    }))
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/42_my-tale/1")
    assert r.status_code == 200
    assert "Body of chapter one." in r.text


def test_reader_404_unknown_story(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/alice/nonexistent")
    assert r.status_code == 404


def test_reader_path_traversal_blocked(output_dir: Path):
    """Path-traversal attempts via author or dir_name must be rejected."""
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/read/..%2F..%2F/42_x")
    assert r.status_code in (400, 404)
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Create `wattpad_crawler/web/templates/reader.html`**

```html
{% extends "base.html" %}
{% block title %}{{ meta.title }} — Wattpad Crawler{% endblock %}
{% block content %}
  <div class="reader">
    {% if not chapter %}
      <h1>{{ meta.title }}</h1>
      <p class="muted">by {{ meta.author_username }}</p>
      {% if meta.description %}<p>{{ meta.description }}</p>{% endif %}
      {% for tag in meta.tags %}<span class="tag">{{ tag }}</span>{% endfor %}
      <h2>Chapters</h2>
      <ol>
        {% for p in meta.parts %}
          <li>
            <a href="/read/{{ author }}/{{ dir_name }}/{{ p.ordinal }}">{{ p.title }}</a>
          </li>
        {% endfor %}
      </ol>
      <p>
        <a href="/library">← Library</a> ·
        Other formats:
        {% if has_epub %}<a href="/library/output/{{ author }}/{{ dir_name }}/epub">EPUB</a>{% endif %}
        {% if has_html %}<a href="/library/output/{{ author }}/{{ dir_name }}/html">HTML</a>{% endif %}
        {% if has_txt %}<a href="/library/output/{{ author }}/{{ dir_name }}/txt">TXT</a>{% endif %}
      </p>
    {% else %}
      <p class="muted"><a href="/read/{{ author }}/{{ dir_name }}">← {{ meta.title }}</a></p>
      <h1>{{ chapter.title }}</h1>
      <div class="chapter-body">
        <pre>{{ chapter.body }}</pre>
      </div>
      <p>
        {% if chapter.prev_ord %}<a href="/read/{{ author }}/{{ dir_name }}/{{ chapter.prev_ord }}">← Previous</a>{% endif %}
        {% if chapter.next_ord %} · <a href="/read/{{ author }}/{{ dir_name }}/{{ chapter.next_ord }}">Next →</a>{% endif %}
      </p>
    {% endif %}
  </div>
{% endblock %}
```

- [ ] **Step 4: Add the routes** to `wattpad_crawler/web/routes.py`

```python
def _resolve_story_dir(cfg, author: str, dir_name: str) -> Path:
    """Resolve a (author, dir_name) request to an absolute path under stories/.
    Rejects path traversal."""
    stories_root = (cfg.output_dir / "stories").resolve()
    target = (cfg.output_dir / "stories" / author / dir_name).resolve()
    if not target.is_relative_to(stories_root):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.exists() or not (target / "metadata.json").exists():
        raise HTTPException(status_code=404, detail="story not found")
    return target


@router.get("/read/{author}/{dir_name}", response_class=HTMLResponse)
def reader_toc(request: Request, author: str, dir_name: str) -> HTMLResponse:
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
    out = sd / "output"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "author": author,
            "dir_name": dir_name,
            "meta": meta,
            "chapter": None,
            "has_epub": any(out.glob("*.epub")) if out.exists() else False,
            "has_html": any(out.glob("*.html")) if out.exists() else False,
            "has_txt": any(out.glob("*.txt")) if out.exists() else False,
        },
    )


@router.get("/read/{author}/{dir_name}/{ordinal}", response_class=HTMLResponse)
def reader_chapter(
    request: Request, author: str, dir_name: str, ordinal: int
) -> HTMLResponse:
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
    parts = sorted(meta.get("parts", []), key=lambda p: p.get("ordinal", 0))
    p = next((q for q in parts if int(q.get("ordinal", 0)) == ordinal), None)
    if p is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    prefix = f"{ordinal:02d}_{p['part_id']}_"
    txt_files = list((sd / "parts").glob(f"{prefix}*.txt"))
    body = txt_files[0].read_text(encoding="utf-8") if txt_files else "(missing chapter body)"

    ords = [int(q["ordinal"]) for q in parts]
    prev_ord = max((o for o in ords if o < ordinal), default=None)
    next_ord = min((o for o in ords if o > ordinal), default=None)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "author": author,
            "dir_name": dir_name,
            "meta": meta,
            "chapter": {
                "title": p.get("title", ""),
                "body": body,
                "prev_ord": prev_ord,
                "next_ord": next_ord,
            },
        },
    )


@router.get("/library/output/{author}/{dir_name}/{fmt}")
def library_output(request: Request, author: str, dir_name: str, fmt: str) -> FileResponse:
    """Serve the EPUB / HTML / TXT artifact from <story>/output/."""
    if fmt not in ("epub", "html", "txt"):
        raise HTTPException(status_code=404, detail="unknown format")
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    candidates = list((sd / "output").glob(f"*.{fmt}"))
    if not candidates:
        raise HTTPException(status_code=404, detail=f"no .{fmt} artifact")
    media = {"epub": "application/epub+zip", "html": "text/html", "txt": "text/plain"}[fmt]
    return FileResponse(candidates[0], media_type=media, filename=candidates[0].name)
```

- [ ] **Step 5: Run tests — should pass.**

- [ ] **Step 6: Commit**

```bash
git add wattpad_crawler/web/routes.py wattpad_crawler/web/templates/reader.html tests/unit/test_web_routes.py
git commit -m "feat(web): /read story TOC + chapter view + artifact downloads"
```

---

## Phase 7 — CLI Wiring + Polish

### Task 16: `wattpad-crawler serve` subcommand

**Files:**
- Modify: `wattpad_crawler/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Add failing tests**

```python
def test_parser_serve_command():
    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.cmd == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_parser_serve_with_custom_host_port():
    parser = build_parser()
    args = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_main_serve_invokes_uvicorn(output_dir, monkeypatch):
    captured = {}

    def fake_run(app, host, port, log_level):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    rc = main(["--output", str(output_dir), "serve", "--host", "127.0.0.1", "--port", "9000"])
    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9000
```

- [ ] **Step 2: Run — should fail.**

- [ ] **Step 3: Update `wattpad_crawler/cli.py`**

In `build_parser`, add the `serve` subcommand at the bottom (just before `return p`):

```python
    sp_serve = sub.add_parser("serve", help="Run the local web UI")
    sp_serve.add_argument("--host", default="127.0.0.1")
    sp_serve.add_argument("--port", type=int, default=8000)
```

In `main`, add a branch for `serve` (after the existing branches, before `return 0`):

```python
        elif args.cmd == "serve":
            # serve owns its own client/manifest lifecycle inside JobRunner threads;
            # close the ones main() opened so we don't leak them.
            manifest.close()
            client.close()
            import uvicorn
            from wattpad_crawler.web.app import build_app
            app = build_app(cfg)
            uvicorn.run(app, host=args.host, port=args.port, log_level="info")
            return 0
```

The existing `try`/`finally` will then no-op the closes (since `Manifest.close` is idempotent and `RateLimitedClient.close` is too).

- [ ] **Step 4: Run tests — should pass.**

```bash
pytest tests/unit/test_cli.py -v
```

- [ ] **Step 5: Smoke-test the server starts (manual; mark as passing after starting and Ctrl-C)**

```bash
wattpad-crawler --output ./wattpad-archive serve --port 8765
# In another terminal:
curl http://localhost:8765/_health
# Expected: {"status":"ok"}
# Then Ctrl-C the server.
```

- [ ] **Step 6: Commit**

```bash
git add wattpad_crawler/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): serve subcommand — run local web UI via uvicorn"
```

---

### Task 17: Update README with web UI instructions

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current README**

```bash
cat README.md
```

- [ ] **Step 2: Add a new section** above the "Output Layout" section. Insert this between "Usage" and "Output Layout":

```markdown
## Web UI

For a friendlier experience, run the local web UI:

```bash
wattpad-crawler --output ./wattpad-archive serve
```

Then open <http://127.0.0.1:8000> in your browser. Features:

- **Setup:** paste your cookie, save (no terminal needed for this).
- **Dashboard:** click a button to archive your library, a reading list, or a single story.
- **Live progress:** watch chapters and comments stream in via Server-Sent Events.
- **Library:** browse archived stories by cover.
- **Reader:** read chapters in a clean view directly from your local archive.

The web UI calls the same code as the CLI — `_state.sqlite` is the single source of truth for both.

To bind to all interfaces (e.g. for a homelab):

```bash
wattpad-crawler serve --host 0.0.0.0 --port 8000
```
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add web UI section to README"
```

---

### Task 18: Final test sweep + lint + smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd "D:/Dev/Wattpad Crawler"
pytest -v
```

Expected: all unit tests pass (count = 121 + new tests added in this plan; should be ~145+). Integration test still skipped.

- [ ] **Step 2: Run ruff lint**

```bash
ruff check wattpad_crawler tests
```

Expected: All checks passed.

- [ ] **Step 3: Verify the full CLI**

```bash
wattpad-crawler --help
wattpad-crawler serve --help
wattpad-crawler library --help
```

Expected: each prints help text including `serve` in the main list.

- [ ] **Step 4: Smoke-test the web UI end-to-end (manual)**

```bash
# Start in one terminal:
wattpad-crawler --output ./wattpad-archive serve --port 8765
```

In a browser, visit:
- `http://127.0.0.1:8765/` — dashboard renders, three buttons visible.
- `http://127.0.0.1:8765/setup` — setup form renders.
- `http://127.0.0.1:8765/library` — library page renders (empty if no archive yet).
- `http://127.0.0.1:8765/_health` — `{"status":"ok"}`.

Then Ctrl-C.

- [ ] **Step 5: Final commit if anything was fixed**

```bash
git status -s
# If anything changed:
git add -u
git commit -m "chore: final web UI sweep + fixes"
```

---

## Done

At this point:
- The crawler ships with both a CLI and a friendly local web UI on top of the same `jobs.py`.
- A user can paste their cookie in the browser, click a button, and watch progress live.
- Browse and read archived stories without leaving the browser.
- The CLI continues to work exactly as before (the `progress` callback is optional and defaults to no-op).
