# Architecture Research

**Domain:** Python personal archiving tool — hardening milestone integration
**Researched:** 2026-05-03
**Confidence:** HIGH (all evidence from direct codebase inspection; no speculative sources)

## Standard Architecture

### System Overview (Current State)

```
┌───────────────────────────────────────────────────────────────────┐
│                      Entry Layer                                   │
│  ┌──────────────────┐              ┌──────────────────────────┐   │
│  │  cli.py:main()   │              │ web/routes.py (FastAPI)  │   │
│  └────────┬─────────┘              └────────────┬─────────────┘   │
│           │                                     │                 │
│           │                         ┌───────────▼──────────────┐  │
│           │                         │ web/runner.py JobRunner  │  │
│           │                         │  (daemon thread per job) │  │
│           └──────────────────┬──────┘                          │  │
└──────────────────────────────┼─────────────────────────────────┘  │
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│                  Archive Pipeline (jobs.py)                      │
│   archive_story() / archive_many()  +  JobDeps (DI callables)  │
└────┬──────────────────┬─────────────────────────────────────────┘
     │                  │
┌────▼──────┐   ┌───────▼──────────────────────────────────────┐
│ api/      │   │ scrape/chapter_html.py   extract_chapter()   │
│ story.py  │   └───────────────────────────────────────────────┘
│ user.py   │
│ comments  │   ┌──────────────────────────────────────────────┐
└────┬──────┘   │ archive/  state.py(Manifest) + store.py     │
     │          │           SQLite WAL   + atomic file I/O    │
     │          └──────────────────────────────────────────────┘
     │
┌────▼──────────────────────────────────────────────┐
│  client.py  RateLimitedClient + TokenBucket       │
│  (shared per-job; one instance per JobRunner thread)│
└───────────────────────────────────────────────────┘

Post-archive (sequential, after all parts):
┌──────────────────────────────────────────────────┐
│  render/txt.py  render/html.py  render/epub.py   │
│  Read all parts into memory → write single file  │
└──────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Current Shape |
|-----------|----------------|---------------|
| `jobs.py:archive_story` | Orchestrates full per-story pipeline | Sequential for-loop over parts |
| `jobs.py:JobDeps` | DI callables for fetch/parse — swapped in tests | @dataclass of Callables |
| `client.py:RateLimitedClient` | Rate-limited HTTP with token-bucket | One per job; thread-safe bucket |
| `api/comments.py` | Paginated comment fetch + recursive reply parse | Unbounded recursion on replies |
| `scrape/chapter_html.py:extract_chapter` | BeautifulSoup HTML→ChapterContent | Returns empty silently on selector miss |
| `archive/state.py:Manifest` | SQLite CRUD for story/part status + body_hash | WAL mode; one connection per caller |
| `archive/store.py` | Atomic file I/O, directory layout | PID+TID temp suffix |
| `render/*.py` | Accumulate all parts in memory → write artifact | Full story in RAM |
| `web/runner.py:JobManager` | In-memory job registry | Unbounded growth |
| `web/runner.py:JobRunner` | Daemon thread per submitted job | No upper bound on threads |

---

## Hardening Features — Integration Design

### 1. In-Story Chapter Parallelism

**Where the executor lives:** Inside `archive_story()` in `jobs.py`, replacing the sequential `for part in story.parts` loop. Do NOT push it into `JobDeps` — `JobDeps` abstracts individual fetch callables, not scheduling policy. The executor is a scheduling concern that belongs to the pipeline orchestrator.

**Concrete shape:**
```python
# jobs.py
from concurrent.futures import ThreadPoolExecutor, as_completed

def _archive_part(
    part: Part,
    client: RateLimitedClient,   # shared — token bucket serializes network
    manifest: Manifest,           # shared — SQLite WAL, write calls serialized per below
    ...
) -> tuple[str, str]:             # (part_id, "done" | "failed: ...")
    ...

with ThreadPoolExecutor(max_workers=cfg.workers_per_story) as pool:
    futures = {pool.submit(_archive_part, part, ...): part for part in todo_parts}
    for fut in as_completed(futures):
        part_id, status = fut.result()
        emit(...)   # see ordering below
```

**`RateLimitedClient` sharing:** The existing `TokenBucket` is already `threading.Lock`-protected. Multiple worker threads calling `_bucket.take()` will block correctly and share the rate-limit budget. No change required to `client.py`.

**SQLite write serialization:** `Manifest.set_part_status()` currently opens a `with self._conn:` transaction. SQLite in WAL mode handles concurrent writers by serializing them at the database level, but Python's `sqlite3` module also holds the GIL during write operations, so concurrent threads calling `set_part_status()` from different workers will serialize correctly without application-level locking. No new Manifest lock is needed. The only invariant to preserve: each worker must call `set_part_status("in_progress")` before doing I/O and `set_part_status("done" | "failed")` after — this is already the existing pattern, it just runs across threads now.

**Progress event ordering:** `job.emit()` uses a `threading.Lock` before appending to `events`, so events themselves are safely appended. However `as_completed()` returns futures in completion order, not submission order. The SSE stream already replays all events in list order, so out-of-ordinal part events (`part.done` for ordinal 5 before ordinal 2) will appear in the stream. This is acceptable — the web UI should display by part ordinal, not by event arrival order. The `part.done` event carries `ordinal` in its data dict, which the UI already has. Document this in the UI rather than adding re-ordering logic.

**`body_hash` dedup with out-of-order fetches:** The existing cache check `if existing and existing["status"] == "done": skip` runs at the start of `_archive_part`, before the fetch. Multiple threads checking the same `part_id` simultaneously is safe because:
- The initial check is a read; SQLite WAL allows concurrent reads.
- If two workers somehow raced on the same part (impossible with distinct `story.parts` list items — each Part has a unique `part_id`), the second `set_part_status("done")` would be an idempotent upsert. The `body_hash` is derived deterministically from `content.text` so both would write the same hash.
- No additional dedup logic required.

**Config wire-up:** `cfg.workers_per_story` is currently loaded but never read. The executor uses it directly. No new config fields needed.

**Files changed:** `jobs.py` only (the executor replaces the loop). `client.py` and `archive/state.py` unchanged.

---

### 2. Cookie Validation

**Where it lives:** A new module `wattpad_crawler/auth.py` with a single public function `validate_cookie(client: RateLimitedClient) -> None` (raises `AuthError` on failure). This is not part of `RateLimitedClient` itself — the client's job is transport, not auth-level semantics. It is not in `api/story.py` — validation is a startup concern, not a per-request concern.

**New module shape:**
```python
# wattpad_crawler/auth.py
class AuthError(Exception):
    pass

def validate_cookie(client: RateLimitedClient) -> None:
    """Make a cheap authenticated API call. Raises AuthError if unauthenticated."""
    resp = client.get("https://www.wattpad.com/api/v3/users/me")
    if resp.status_code == 401 or resp.status_code == 403:
        raise AuthError("Wattpad cookie is invalid or expired.")
    # A 200 with a guest/anonymous user body also indicates no auth —
    # check for the presence of an 'id' field in the response.
    data = resp.json()
    if not data.get("id"):
        raise AuthError("Cookie accepted but returned anonymous user — cookie may be expired.")
```

**Call sites:**
- CLI: `cli.py:main()` — after `RateLimitedClient` is created, before calling `archive_story()`. Wrap in `try/except AuthError` and print a clear message, then exit.
- Web `/setup` POST: `web/routes.py` — after saving the cookie, call `validate_cookie()` with a temporary client. Surface failure as a form error, not a 500.
- Web job start: `web/routes.py` — before `runner.submit()`, call `validate_cookie()` with the job's client. Return HTTP 400 with message if it fails.

**Auth failure mid-job detection:** Mid-job 401/403 responses come back through `RateLimitedClient.get()`, which currently calls `resp.raise_for_status()` after exhausting retries. That raises `httpx.HTTPStatusError`. The part pipeline catches all exceptions with `except Exception as e` and marks the part "failed". This surfaces as `part.failed` events in the SSE stream. No new mechanism needed for mid-job detection — it already fails loudly at the part level. The circuit-breaker (item 3 below) will amplify this signal to abort the whole job.

**Files added/changed:** New `wattpad_crawler/auth.py`. Changes to `cli.py`, `web/routes.py`.

---

### 3. Circuit-Breakers

**Two distinct breakers, not one abstraction.** They trigger on different signals, have different thresholds, and fail for different reasons. Merging them would muddle the error messages.

| Breaker | Signal | Threshold | Consequence |
|---------|--------|-----------|-------------|
| Extraction-empty | `ChapterContent.text` empty while `len(raw_html) > 5000` | N consecutive parts | Abort story, loud error |
| HTTP-wall | Non-200/429 HTTP status codes (4xx/5xx) exhausting retries | N consecutive parts | Abort story, loud error |

**Where they live:** Both live in `jobs.py` as lightweight counter objects scoped to a single `archive_story()` invocation. They are NOT per-client (the client handles transport retries, not semantic-level failure counts). They are NOT per-job-runner (the runner handles thread lifecycle). They observe part outcomes inside the part loop.

**Concrete shape:**
```python
# jobs.py — add inside archive_story(), initialized before the part loop

@dataclass
class _Breaker:
    name: str
    threshold: int
    _count: int = 0

    def record_failure(self) -> None:
        self._count += 1
        if self._count >= self.threshold:
            raise CircuitOpenError(
                f"{self.name}: {self._count} consecutive failures — aborting."
            )

    def record_success(self) -> None:
        self._count = 0

class CircuitOpenError(Exception):
    pass

extraction_breaker = _Breaker("extraction-empty", threshold=cfg.extraction_empty_threshold)
http_wall_breaker   = _Breaker("http-wall", threshold=cfg.http_wall_threshold)
```

**Integration with parallel executor:** With `ThreadPoolExecutor` and `as_completed()`, breaker state must be protected from concurrent writes. Use a `threading.Lock` on the `_Breaker` object. When a `CircuitOpenError` is raised inside `_archive_part`, it propagates out of the future. The `as_completed()` consumer re-raises it, which propagates out of `archive_story()`, which causes the whole job to fail. Outstanding futures are not cancelled immediately — `ThreadPoolExecutor` does not support future cancellation for running tasks — but they will complete normally and their results will be ignored as the exception unwinds. This is acceptable; the parts that complete after the circuit opens are free gifts.

**Where they raise:** They raise `CircuitOpenError` which propagates out of `archive_story()` entirely (not caught by the per-part `except Exception`). The outer `JobRunner._run()` catches it as a job-level failure, calls `job.set_failed()`. The web UI and CLI both show this as job failure with a clear error string. This is the correct behavior — circuit-open is not a recoverable per-part condition.

**Config additions:** `extraction_empty_threshold: int = 3` and `http_wall_threshold: int = 5` added to `Config` dataclass and `_DEFAULT_TOML` in `config.py`.

**Files changed:** `jobs.py` (add `_Breaker`, `CircuitOpenError`, integrate in part loop). `config.py` (add two threshold fields). No changes to `client.py` or `api/`.

---

### 4. Bounded Comment Recursion

**Where it lives:** Localized fix inside `api/comments.py:_parse_one()`. This is a localized concern — comment parsing is entirely self-contained, and a shared traversal utility would be over-engineering for a single call site.

**Change:**
```python
def _parse_one(raw: dict[str, Any], *, depth: int = 0, max_depth: int = 10) -> Comment | None:
    ...
    if depth < max_depth:
        replies = [
            c
            for c in (_parse_one(r, depth=depth + 1, max_depth=max_depth)
                      for r in replies_raw if isinstance(r, dict))
            if c is not None
        ]
    else:
        replies = []
        if replies_raw:
            logger.warning("comment replies truncated at depth %d for comment %s", depth, cid)
    ...
```

`parse_comments_page` passes the `max_depth` through to `_parse_one` (or uses the default). The max depth can be a module constant `_MAX_REPLY_DEPTH = 10` — no need to expose in config for a personal tool.

**Files changed:** `api/comments.py` only.

---

### 5. HTML Sanitization

**Where it belongs: at extract-time in `scrape/chapter_html.py`.** Justification:

- **At extract-time** (chosen): sanitized HTML is stored in `paragraph["html"]` in `.json` files and in the `.html` part files. Every downstream consumer (store, renderers, web reader) receives clean data. There is one sanitization call per paragraph, once, at the moment the data is first materialized. The invariant is simple: "the `html` field in a stored paragraph is always sanitized."

- **At store-time** (`archive/store.py`): Would require `store.py` to take a dependency on a sanitization library, mixing I/O concerns with content-security concerns. It also does not protect the in-memory `ChapterContent` object used between extract and store.

- **At render-time** (`render/html.py`, `render/epub.py`): Too late. Raw HTML is already in the `.json` files on disk. A future reader or alternative renderer that reads those files directly would bypass sanitization. Also, render-time sanitization means the stored archive is potentially unsafe, which contradicts the goal.

**Library choice:** `nh3` (Rust-backed, maintained, actively developed as of 2024-2026, drop-in for bleach's most common use case). Add to `pyproject.toml` dependencies. Bleach is in maintenance-only mode.

**Change in `chapter_html.py`:**
```python
import nh3

ALLOWED_TAGS = {"a", "b", "em", "i", "strong", "u", "br", "img", "p", "span"}
ALLOWED_ATTRS = {"a": {"href"}, "img": {"src", "alt"}}

# Inside extract_chapter(), in the paragraph loop:
raw_html = para.decode_contents()
safe_html = nh3.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)
paragraphs.append({"id": pid, "text": para.get_text(" ", strip=True), "html": safe_html})
```

**Files changed:** `scrape/chapter_html.py`. `pyproject.toml` (add `nh3` dependency). No changes to renderers or store.

---

### 6. Streamed Rendering

**TXT and HTML (straightforward streaming):** Replace the in-memory `chunks` list with direct incremental writes to an open file handle. Both renderers already know the output path before iterating parts. The pattern is: open output file in write mode, write the header, iterate parts writing each chapter immediately, close file. Use `atomic_write_text` for the final rename, or open a temp file and rename at end (matching the existing atomic pattern).

Revised shape for `render_txt`:
```python
def render_txt(story_dir_path: Path) -> None:
    meta = json.loads(...)
    tmp = _tmp_path(out_path)
    with tmp.open("w", encoding="utf-8") as f:
        f.write(f"{meta['title']}\nby {meta['author_username']}\n\n")
        for p in sorted(meta["parts"], key=lambda x: x["ordinal"]):
            candidates = list(parts_dir.glob(...))
            if not candidates:
                continue
            f.write(f"\n\n========\n{p['title']}\n========\n\n")
            # Stream the chapter body rather than read_text() all at once:
            with candidates[0].open(encoding="utf-8") as ch:
                shutil.copyfileobj(ch, f)
            f.write("\n")
    os.replace(tmp, out_path)
```

Same approach for `render_html` — open temp file, write `_HEAD`, iterate parts writing each chapter `<div>`, write closing tags, rename.

**EPUB — EbookLib constraint:** `ebooklib.epub.write_epub()` writes the entire EPUB at once from the in-memory `EpubBook` object. There is no incremental write API in EbookLib. The book object itself must hold all `EpubHtml` chapter objects before `write_epub()` is called.

The memory problem is that each `EpubHtml` chapter's `.content` attribute holds the full chapter HTML string in RAM. For a 500-chapter story, this is 500 HTML strings simultaneously in memory.

**Chosen EPUB strategy:** Avoid holding the full chapter HTML in the `EpubHtml.content` attribute. Instead:
1. Iterate parts, reading each chapter file.
2. Create `EpubHtml` objects as before, but set `.content` to the chapter HTML directly — this is unavoidable with EbookLib's API.
3. After `write_epub()` completes, delete the `book` object explicitly to free memory.

This does NOT solve the O(N) memory problem for very large stories. For the current milestone scope ("personal use, reasonable story sizes"), this is an acceptable non-fix. The `PROJECT.md` explicitly says: "Defer EPUB rendering to streaming until measured." The correct note for the roadmap is: profile before optimizing; if profiling shows EPUB is the bottleneck, the mitigation is to write a custom EPUB serializer that streams directly into a ZipFile (EPUB is a ZIP). That is out of scope for this milestone.

**For this milestone:** Stream TXT and HTML. Document EPUB as a known limitation. Add a comment in `render/epub.py` explaining the constraint and the ZipFile escape path.

**Files changed:** `render/txt.py`, `render/html.py` (streaming refactor). `render/epub.py` (comment + explicit del book, no functional change).

---

### 7. Job Pruning

**Where it lives:** Inside `JobManager` itself, called at the end of `create()`. This is a pull-on-write approach: pruning happens when a new job is created, not on a background timer. Rationale:
- No background timer thread needed (simpler, fewer moving parts).
- `list_jobs()` is called frequently for SSE and dashboard rendering — adding pruning there would make a read operation perform writes, which is surprising.
- `create()` is the right moment: "make room when adding a new job."

**Concrete shape:**
```python
# web/runner.py
_MAX_JOBS = 100       # keep last N completed jobs
_MAX_EVENTS = 1000    # per job

class JobManager:
    def create(self, kind: str, args: dict) -> Job:
        job = Job(...)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._prune()          # trim while lock is held
        return job

    def _prune(self) -> None:
        """Must be called under self._lock."""
        while len(self._order) > _MAX_JOBS:
            oldest_id = self._order.pop(0)
            # Only prune if not currently running
            oldest = self._jobs.get(oldest_id)
            if oldest and oldest.status in (JobStatus.running, JobStatus.pending):
                self._order.insert(0, oldest_id)  # put it back
                break
            self._jobs.pop(oldest_id, None)
```

**Event cap:** `Job.emit()` checks `len(self.events) >= _MAX_EVENTS` before appending; if at cap, drop the oldest event (or the new one — dropping oldest is safer for streaming correctness; the SSE stream uses `after_index` so dropped-oldest shifts all indices). Simpler approach: cap by dropping the new event and logging once. Either is fine for personal use.

**Files changed:** `web/runner.py` only.

---

### 8. VCR Integration Test

**Cassette location:** `tests/fixtures/cassettes/archive_story_<story_id>.yaml` (or `.json` depending on the VCR library used). The cassette should be committed to the repository.

**VCR library choice:** `pytest-recording` (wraps `vcrpy`) or `vcrpy` directly. `pytest-recording` integrates cleanly with pytest fixtures via `@pytest.mark.vcr`. It's the standard choice for httpx-based code with the `vcrpy-httpx` integration or by patching at the `httpx.Client` level.

**Entry point exercised:** Call `archive_story()` directly — not the CLI, not the web UI. This is the lowest-level end-to-end path that still exercises the full pipeline: `fetch_story → extract_chapter → fetch_comments → store → render`. Keep the test in `tests/integration/test_end_to_end.py`.

**What the cassette records:** All HTTP responses for a small public story (pick one with ~3 chapters to keep cassette size reasonable). The cassette captures:
- `GET /api/v3/stories/{story_id}` (metadata)
- `GET /story/{part_id}-{slug}` (chapter HTML) × N parts
- `GET /api/v3/parts/{part_id}/comments` × N parts × 2 (inline + end)
- `GET {cover_url}` (cover image)

**Keeping cassettes stable under parallelism:** This is the hardest part of the VCR + parallel design. With `ThreadPoolExecutor`, HTTP requests happen in non-deterministic order. VCR cassettes by default match requests by URI + method and replay in recorded order. If playback order differs from record order, VCR will return wrong responses (e.g., chapter 3's HTML for the chapter 2 request).

**Mitigation strategy:** Configure VCR to match requests by URI only (not sequence), which is VCR's `record_mode="none"` with `match_on=["uri", "method"]`. Because each chapter URL is unique (different `part_id`), URI-matching correctly routes each request to its recorded response regardless of thread order. Comment requests share the URL template but differ by `part_id`, so URI-matching still works.

**Configuration:**
```python
# tests/conftest.py or test file
import pytest

@pytest.fixture(scope="module")
def vcr_config():
    return {
        "record_mode": "none",          # never hit network in CI
        "match_on": ["method", "uri"],  # order-independent matching
    }
```

**Mark and CI skip:** Tag the integration test `@pytest.mark.integration` (not `@pytest.mark.live` — "live" implies network access; the cassette test is offline). Add `addopts = -m "not live"` to `pyproject.toml` pytest config (already present), and ensure `integration` tests run in CI with `pytest -m integration`.

**Cassette stability after pipeline changes:** The cassette only cares about HTTP traffic, not Python control flow. Adding parallelism changes request ordering but not request content. As long as the URL-based VCR matching is in place, cassette stability is maintained. The one risk: if parallelism causes a race that changes which URLs are requested (e.g., a dedup bug that skips a part), the test will catch it — which is the correct behavior.

**Re-recording:** The cassette must be re-recorded whenever Wattpad changes API response shapes. The `conftest.py` should document how to re-record: `pytest --record-mode=new_episodes -m integration`.

**Files added/changed:** `tests/integration/test_end_to_end.py` (implement), `tests/fixtures/cassettes/` (new cassette files), `tests/conftest.py` (VCR config fixture), `pyproject.toml` (add `pytest-recording` and `vcrpy` to test deps).

---

## Data Flow Changes with Parallelism

### Current Per-Chapter Flow (Sequential)

```
for part in story.parts:
    check_cache(manifest)     → skip or continue
    fetch_html(client)        → raw_html
    extract(raw_html)         → ChapterContent
    fetch_inline(client)      → comments
    fetch_end(client)         → comments
    write_files(store)        → disk
    set_status(manifest)      → SQLite
    emit("part.done")
```

### New Per-Chapter Flow (Parallel Workers)

```
[main thread]
  build todo_parts (filter already-done)
  init extraction_breaker, http_wall_breaker
  |
  ├── ThreadPoolExecutor(max_workers=cfg.workers_per_story)
  │     Each worker thread runs _archive_part(part, client, manifest, ...):
  │       check_cache(manifest)    → skip (read — concurrent-safe, WAL)
  │       fetch_html(client)       → raw_html   (token bucket serializes rate)
  │       extract(raw_html)        → ChapterContent  (CPU, no shared state)
  │       sanitize(content.html)   → safe HTML  (CPU, no shared state)
  │       check extraction_breaker → raise CircuitOpenError if triggered
  │       fetch_inline(client)     → comments   (token bucket serializes rate)
  │       fetch_end(client)        → comments   (token bucket serializes rate)
  │       write_files(store)       → disk       (atomic rename, different paths per part)
  │       set_status(manifest)     → SQLite     (WAL serializes writers)
  │       return ("done", part_id)
  │
  └── as_completed() in main thread:
        emit("part.done" | "part.failed")  → Job events (Lock-protected)
        record_success/failure on breakers → raise CircuitOpenError if open
        [CircuitOpenError propagates → job.set_failed()]

[after executor context exits]
render_txt, render_html, render_epub  (sequential, unchanged)
emit("story.done")
```

**Shared-state access summary under parallelism:**

| Shared Resource | Access Pattern | Safety Mechanism |
|-----------------|----------------|-----------------|
| `TokenBucket._tokens` | Concurrent `take()` from N workers | `threading.Lock` in `TokenBucket` — already correct |
| `Manifest.set_part_status` | Concurrent writes from N workers | SQLite WAL serializes writes at DB level |
| `Manifest.get_part` (cache check) | Concurrent reads | WAL allows concurrent readers |
| `Job.events` (emit) | Concurrent appends from main thread | `threading.Lock` in `Job.emit` — already correct |
| Part files on disk | Each part writes to a unique path | No collision possible by design |
| `_Breaker._count` | Concurrent record_failure/success | Add `threading.Lock` to `_Breaker` |

---

## Build Order with Dependencies

The eight items have the following dependency structure:

```
[4] Bounded comment recursion     — no dependencies, localized
[5] HTML sanitization             — no dependencies, localized
[7] Job pruning                   — no dependencies, localized

[2] Cookie validation             — no dependencies, new module

[3] Circuit-breakers              — depends on [1] conceptually (breakers make
                                    less sense without parallelism, but can ship
                                    independently as sequential breakers first)

[1] In-story parallelism          — must come after [3] if we want
                                    CircuitOpenError to abort a parallel job
                                    correctly (safe either way, but [3] before
                                    [1] avoids rework)

[6] Streamed rendering            — independent, but TXT/HTML streaming
                                    is low-risk and can ship anytime

[8] VCR integration test          — should land AFTER [1] (cassette ordering
                                    fix depends on parallel request order being
                                    nondeterministic) and AFTER [5] (sanitized
                                    HTML should be what's recorded)
```

### Recommended Phase Sequence

**Phase A — Isolated, low-risk fixes (ship together):**
- [4] Bounded comment recursion (`api/comments.py` — 10 lines)
- [5] HTML sanitization (`scrape/chapter_html.py` + `pyproject.toml` — 20 lines)
- [7] Job pruning (`web/runner.py` — 20 lines)

Rationale: All three are purely localized, have no dependencies on each other or on the pipeline shape, and address concrete silent-failure paths. Safe to review and merge as a single PR.

**Phase B — Auth hardening (ships independently):**
- [2] Cookie validation (new `auth.py` + `cli.py` + `web/routes.py`)

Rationale: Standalone new module. No pipeline changes. Can ship before or after Phase A.

**Phase C — Circuit-breakers (depends on Phase A being stable):**
- [3] Extraction-empty + HTTP-wall circuit-breakers (`jobs.py` + `config.py`)

Rationale: The extraction-empty breaker checks `content.text` emptiness — it relies on [5] sanitization being in place so that empty text is actually meaningful (not a sanitization side-effect). Ship after Phase A.

**Phase D — Parallelism (depends on Phase C):**
- [1] In-story chapter parallelism (`jobs.py`)

Rationale: With circuit-breakers already in place, a parallel run that hits an extraction wall will abort cleanly. Without them, a broken selector under parallelism would silently write N empty parts in parallel before anyone noticed.

**Phase E — Rendering + Test (depends on Phase D):**
- [6] Streamed rendering (`render/txt.py`, `render/html.py`)
- [8] VCR integration test (after [1] is stable so cassette order is reproducible)

Rationale: Streaming rendering is independent but the integration test should record against the final pipeline shape (parallel + sanitized) to avoid re-recording after Phase D lands.

### Dependency Graph

```
[4] ──┐
[5] ──┤─→ Phase A ──→ [3] ──→ [1] ──→ [6]
[7] ──┘                              ──→ [8]
[2] ─────────────→ Phase B (independent)
```

### Items Independent vs Cross-Cutting

**Purely local (one file changed):**
- [4] `api/comments.py`
- [7] `web/runner.py`

**One logical unit, two files:**
- [5] `scrape/chapter_html.py` + `pyproject.toml`
- [6] `render/txt.py` + `render/html.py`

**New module + call sites (cross-cutting, but narrow):**
- [2] `auth.py` + `cli.py` + `web/routes.py`

**Invasive to `jobs.py` (cross-cutting to the pipeline):**
- [1] `jobs.py` (loop → executor)
- [3] `jobs.py` + `config.py` (breaker objects in the part loop)

**Cross-cutting test infrastructure:**
- [8] `tests/` + `pyproject.toml` + cassette fixtures

---

## Architectural Patterns

### Pattern 1: Scoped Breaker Object (not a decorator)

**What:** A plain `@dataclass` with a counter and threshold, instantiated inside `archive_story()` for the lifetime of one story's archive. Not a class decorator, not a module-level singleton.

**When to use:** When the failure boundary is "this invocation" rather than "this endpoint globally." A global breaker would bleed state between stories in `archive_many()`.

**Trade-offs:** Simple to test (just construct with threshold=1 and feed failures). Not reusable across different callers without explicit construction — acceptable here since there is only one caller.

### Pattern 2: Auth as a Startup Probe, Not a Per-Request Middleware

**What:** `validate_cookie()` is called once at job start (or setup save), not before every request. Mid-job 401s surface through the existing exception path.

**When to use:** When the auth token is session-based and unlikely to expire mid-job. Wattpad cookies are session cookies that last weeks/months.

**Trade-offs:** Does not catch cookies that expire during a multi-hour archive run. Acceptable for personal use; the existing `part.failed` events expose this when it happens.

### Pattern 3: Pull-based Pruning (write-side triggering)

**What:** `JobManager._prune()` is called inside `create()` under the existing lock. No background thread.

**When to use:** When the pruning rate is naturally bounded by the event that triggers growth (job creation). A 100-job cap means at most 100 cleanup operations ever, each O(1).

**Trade-offs:** Slightly surprising (a create operation also deletes). Mitigated by clear naming and a comment.

### Pattern 4: URI-keyed VCR Matching for Parallel Tests

**What:** Configure `vcrpy` with `match_on=["method", "uri"]` and `record_mode="none"`. Each unique URI maps to exactly one cassette entry; request order is irrelevant.

**When to use:** Any test where the code under test makes HTTP requests in non-deterministic order (threads, async, random backoff).

**Trade-offs:** Fails to detect if the same URL is requested N times but only recorded once (e.g., a retry bug that doubles requests). Mitigate by recording with sufficient initial responses and setting `record_mode="new_episodes"` to catch unexpected new requests.

---

## Anti-Patterns

### Anti-Pattern 1: Executor Inside JobDeps

**What people do:** Put the `ThreadPoolExecutor` inside a `fetch_chapter_html` wrapper injected through `JobDeps`.

**Why it's wrong:** `JobDeps` abstracts single-fetch callables. The executor is a scheduling policy that governs the entire part loop — including cache checks, status writes, comment fetches, and file writes. Hiding it inside one callable leaves the others sequential and breaks the breaker/progress integration.

**Do this instead:** The executor lives in `archive_story()`. `JobDeps` callables remain single-part functions called from worker threads.

### Anti-Pattern 2: Per-Worker Manifest Connection

**What people do:** Open a new `Manifest` connection per worker thread to avoid SQLite contention.

**Why it's wrong:** Multiple `Manifest` instances pointing at the same SQLite file in WAL mode will work, but it defeats the purpose: WAL already handles concurrent writer serialization. Opening extra connections has connection-setup overhead and makes it harder to reason about transaction scope.

**Do this instead:** Share the single `Manifest` instance across worker threads. SQLite WAL + Python's GIL ensure write serialization. Add a comment explaining this intentional sharing.

### Anti-Pattern 3: Sanitizing at Render-Time Only

**What people do:** Sanitize HTML in `render/html.py` and `render/epub.py` just before writing the artifact.

**Why it's wrong:** The stored `.json` and `.html` part files contain raw unsanitized HTML. Any consumer that reads those files (future renderers, the web reader, external tools) bypasses sanitization. The archive is the source of truth; it must be clean.

**Do this instead:** Sanitize at extract-time in `extract_chapter()`. The `html` field in stored paragraphs is always sanitized. Renderers trust the data they read.

### Anti-Pattern 4: Ordered VCR Cassette Replay with Threads

**What people do:** Record a VCR cassette with sequential requests, then run the same test with a parallel executor. The cassette replays in recorded order (VCR default), which does not match the concurrent request order.

**Why it's wrong:** VCR returns chapter 2's HTML for the chapter 3 request, or a comment response for a chapter HTML request. Tests pass accidentally or fail nondeterministically.

**Do this instead:** Use `match_on=["method", "uri"]` in VCR configuration. Each URL maps to its own cassette entry; thread scheduling cannot produce mismatches.

---

## Integration Points

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `archive_story` → worker threads | `_archive_part()` called via `ThreadPoolExecutor.submit()` | Passes client, manifest, deps by reference — all thread-safe |
| worker threads → `Manifest` | Direct method calls on shared instance | WAL mode + Python GIL handles serialization |
| worker threads → `RateLimitedClient` | Direct `client.get()` calls | `TokenBucket` is `threading.Lock`-protected |
| worker threads → `Job.emit` | Callback passed through to `_archive_part` | `Job._lock` serializes event list appends |
| `archive_story` → `_Breaker` | Counter updates in `as_completed()` loop | Needs `threading.Lock` on `_Breaker._count` |
| `auth.py` → `RateLimitedClient` | Direct call before job starts | No state shared; pure function |
| `cli.py` → `auth.py` | Import + call after client construction | New dependency edge: `cli` → `auth` |
| `web/routes.py` → `auth.py` | Import + call at setup POST and job submit | New dependency edge: `routes` → `auth` |

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Wattpad API v3 | `RateLimitedClient.get()` + JSON parse | No official SDK; subject to unannounced changes |
| Wattpad HTML | `RateLimitedClient.get()` + BeautifulSoup | `.page-container` + `data-p-id` selectors are fragile |

---

## Sources

All findings are derived from direct inspection of the codebase files listed below. No external sources were consulted for this architecture dimension — the question is "how do new things integrate into existing code," which is answered entirely from reading that code.

- `wattpad_crawler/jobs.py` — pipeline orchestrator and DI
- `wattpad_crawler/client.py` — `RateLimitedClient`, `TokenBucket`
- `wattpad_crawler/api/comments.py` — recursive comment parsing
- `wattpad_crawler/scrape/chapter_html.py` — `extract_chapter`, `ChapterContent`
- `wattpad_crawler/archive/store.py` — atomic I/O, path layout
- `wattpad_crawler/archive/state.py` — `Manifest` (inspected via architecture doc)
- `wattpad_crawler/web/runner.py` — `JobManager`, `JobRunner`, `Job`
- `wattpad_crawler/render/{txt,html,epub}.py` — renderer internals
- `.planning/codebase/ARCHITECTURE.md` — existing architecture audit
- `.planning/codebase/CONCERNS.md` — concerns audit

---

*Architecture research for: Wattpad Crawler hardening milestone*
*Researched: 2026-05-03*
