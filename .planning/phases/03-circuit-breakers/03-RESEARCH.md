# Phase 3: Circuit-breakers - Research

**Researched:** 2026-05-05
**Domain:** Python control-flow, stdlib threading, httpx exception hierarchy, archive pipeline integration
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** One `Breaker` class, two instances per `archive_story()` call, in new module `wattpad_crawler/circuit_breakers.py`.
- **D-02:** New module `wattpad_crawler/circuit_breakers.py`; four `_lowercase` module constants: `_EXTRACTION_EMPTY_CONSECUTIVE = 3`, `_HTTP_WALL_CONSECUTIVE = 5`, `_TEXT_THRESHOLD = 100`, `_HTML_THRESHOLD = 5000`. Tests monkeypatch these constants.
- **D-03:** `Breaker.record_failure(...)` raises `CircuitOpenError` itself when threshold crossed. `record_success()` resets counter.
- **D-04:** Thread-safe via `threading.Lock` per Breaker instance. Lock acquired inside `record_failure`/`record_success`; `CircuitOpenError` raised after lock release.
- **D-05:** `CircuitOpenError(Exception)` in `circuit_breakers.py`. Constructor: `CircuitOpenError(message: str, *, kind: Literal["extraction_empty", "http_wall"], threshold: int, count: int, recent: list[dict])`.
- **D-06:** Error message text: `extraction_empty` → `"selector likely changed: 3 consecutive chapters had <100 chars text from >5KB HTML"`; `http_wall` → `"HTTP wall: 5 consecutive non-200/non-404 responses {status_codes}"`.
- **D-07:** Counter semantics — reset on success. 404 resets nothing, does not increment. Successful HTTP (200) resets `http_wall`. Successful extraction resets `extraction_empty`. Recent-tape bounded to threshold size.
- **D-08:** Manifest status for extraction-empty part is `body_text_failed`.
- **D-09:** Write only `raw_html.html` on extraction-empty parts. `write_part_files()` either gains `raw_html_only: bool = False` or splits into two helpers (Claude's Discretion).
- **D-10:** `parts.last_error` for `body_text_failed` = `"extraction empty: text={n} chars, html={m} bytes"`.
- **D-11:** `breaker.opened` SSE event payload schemas (see CONTEXT.md for full schema).
- **D-12:** `archive_story()` catches `CircuitOpenError`, emits `breaker.opened`, re-raises. Order: `except AuthFailedError` → `except CircuitOpenError` → `except Exception` (broad).
- **D-13:** 404 → `"gone"` mapping in `archive_story()`'s broad except, not in `RateLimitedClient.get()`.
- **D-14:** HTTP-wall increments via `record_failure(status_code=..., url=...)` from the broad except. (See Control Flow Concern below — implementation requires a nested try.)
- **D-15:** HTTP-wall counts only HTTP 4xx (excluding 404) and 5xx. `httpx.RequestError` transport errors NOT counted.
- **D-16:** 429 retries inside `RateLimitedClient.get()`. Final 429 outcome counts as ONE http_wall increment.

### Claude's Discretion

- `recent`-tape data structure — `collections.deque(maxlen=threshold)` is natural fit.
- Splitting `write_part_files()` — `raw_html_only=True` flag vs. two helpers; pick whichever keeps callers cleaner.
- Whether `http_wall.record_failure` accepts full `httpx.HTTPStatusError` or `(status_code, url)` primitives — primitives preferred (keeps Breaker free of httpx imports).
- Test fixture shapes and monkeypatch strategy (four constants down to small values).
- Whether to `logger.warning` on each `record_failure` increment before trip.
- Boundary edge case: `text == ""` AND `html < 5 KB` → no increment (trips only when BOTH `text < 100` AND `html > 5 KB`).

### Deferred Ideas (OUT OF SCOPE)

- Auto-resume / half-open state (RES-V2-01).
- Persisted breaker state across runs.
- HTTP-wall counting `httpx.RequestError` transport errors.
- Pool-wide / multi-story breaker state.
- UI banner / styling for `breaker.opened`.
- Lifting thresholds into TOML config.
- Recording breaker-fire analytics across runs.
- Migrating off `data-p-id` selector.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RES-01 | Extraction-empty circuit-breaker: text < 100 chars while raw HTML > 5 KB; opens after 3 consecutive; aborts with "selector likely changed" error | D-02 constants verified in code; `ChapterContent.text` confirmed as post-sanitization string; `body_text_failed` PartStatus confirmed present in `models.py:4` |
| RES-02 | HTTP-wall circuit-breaker: 4xx (excl. 404) and 5xx consecutive; opens after 5; aborts with recent error pattern | `httpx.HTTPStatusError` inspection pattern verified in `client.py`; `raise_for_status()` surfaces non-2xx as `HTTPStatusError`; 429 retry behavior confirmed in `client.py:120–131` |
| RES-03 | Both breakers scoped per `archive_story()` call; emit `breaker.opened` progress events | Per-part try/except structure verified at `jobs.py:115–174`; `emit` callback pattern confirmed; `JobRunner` top-level except confirmed in `web/runner.py` |
</phase_requirements>

---

## Summary

Phase 3 adds two circuit-breakers to `archive_story()` in `jobs.py`. All design decisions (D-01 through D-16) are locked. Research confirms the locked decisions are internally consistent with the existing codebase — the `PartStatus` literals, `Manifest.set_part_status()` signature, `emit()` callback pattern, and `JobDeps` injection structure all support the planned implementation without modification beyond the three files identified in CONTEXT.md.

One **critical control-flow subtlety** was discovered during Python semantics verification (see "D-14 Control Flow Concern" below): `CircuitOpenError` raised from inside the broad `except Exception` body does NOT propagate to the sibling `except CircuitOpenError` clause in the same `try` block. It propagates past the entire `try` block to the caller. The implementation must use a **nested try inside the broad except body** to catch and re-emit the `breaker.opened` event before re-raising. This is a deviation from D-14's prose description but is consistent with D-14's intent — the section says "flies past the broad except", which is correct; the planner must implement this via a nested try, not via the sibling handler.

The `auth.py` precedent matches CONTEXT.md's claims exactly. The `write_part_files()` signature is confirmed. All four module constants are correctly specified. The test infrastructure (monkeypatch pattern, `_make_deps()`, `output_dir` fixture) is fully in place and supports Phase 3 additions without changes to `conftest.py`.

**Primary recommendation:** Implement `circuit_breakers.py` first, then modify `jobs.py` using the nested-try pattern inside the broad except for HTTP-wall, and add `raw_html_only` kwarg to `write_part_files()` for extraction-empty parts.

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 3 |
|-----------|-------------------|
| Python 3.11+; stdlib + minimal deps | `circuit_breakers.py` uses only `threading`, `collections`, `typing` — no new deps |
| `ruff` (line 100, rules E/F/I/UP/W) | All new code must pass `ruff check` and `ruff format` |
| No platform-specific paths or APIs | `threading.Lock` is cross-platform; no Windows-specific code |
| No schema breaks to `_state.sqlite` without migration | `body_text_failed` and `gone` are pre-existing `PartStatus` literals; no schema change |
| `pybreaker`/`circuitbreaker` libraries explicitly excluded | REQUIREMENTS §"Out of Scope" — stdlib-only inline implementation required |
| Single-process; no multi-user | Per-story breaker scope is correct; no pool-wide state |
| Backwards compatibility | Existing archives continue to work; no directory layout changes |

---

## Standard Stack

### Core (stdlib only — no new dependencies)

| Module | Version | Purpose | Why Standard |
|--------|---------|---------|--------------|
| `threading.Lock` | stdlib | Thread-safe counter mutation | Established pattern in `client.py:TokenBucket` and `web/runner.py:Job._lock` |
| `collections.deque` | stdlib | Bounded recent-tape (auto-evicts on overflow) | Natural fit for `maxlen=threshold` circular buffer |
| `typing.Literal` | stdlib | `kind: Literal["extraction_empty", "http_wall"]` type annotation | Project-wide pattern (`PartStatus`, `StoryStatus` in `models.py`) |
| `httpx.HTTPStatusError` | already installed (>=0.27) | Inspect status codes in broad except | Already used in `client.py`; `e.response.status_code` and `e.request.url` confirmed attributes |

**No new packages.** [VERIFIED: pyproject.toml and existing client.py imports]

### Supporting (existing project modules)

| Module | Location | Used By Phase 3 |
|--------|----------|-----------------|
| `ChapterContent` | `scrape/chapter_html.py` | `content.text` length check for extraction-empty heuristic |
| `Manifest.set_part_status()` | `archive/state.py:110` | Already accepts `last_error=` keyword; used for `body_text_failed` and `gone` |
| `store.write_part_files()` | `archive/store.py:107` | Modified to accept `raw_html_only=True` or split into two helpers |
| `JobDeps` | `jobs.py:24` | Test injection pattern; `parse_chapter` and `fetch_chapter_html` are the two callables Phase 3 tests override |

[VERIFIED: all files read directly in this session]

---

## Architecture Patterns

### Recommended Project Structure

```
wattpad_crawler/
├── circuit_breakers.py     # NEW: Breaker class, CircuitOpenError, 4 constants
├── auth.py                 # PRECEDENT: mirrors circuit_breakers.py shape
├── client.py               # UNCHANGED (TokenBucket Lock pattern referenced)
├── jobs.py                 # MODIFIED: import Breaker/CircuitOpenError, 3 edits
└── archive/
    └── store.py            # MODIFIED: write_part_files() gains raw_html_only kwarg
tests/
└── unit/
    ├── test_circuit_breakers.py   # NEW: Breaker isolation tests
    └── test_jobs.py               # EXTENDED: breaker integration tests (append-only)
```

### Pattern 1: Breaker Class Shape (mirrors auth.py)

`auth.py` structure (verified at `wattpad_crawler/auth.py:1-113`):
- Module docstring
- `logger = logging.getLogger(__name__)`
- `_PROBE_URL = "..."` (module constant, lowercase with leading underscore)
- `class AuthError(Exception)`: direct Exception subclass, one-liner docstring
- `class AuthFailedError(AuthError)`: subclass, `__init__` with keyword-only args
- `def validate_cookie(client)`: uses `logger.warning`, raises custom exceptions

`circuit_breakers.py` follows the same shape: [VERIFIED: auth.py read in this session]

```python
# Source: auth.py pattern, verified 2026-05-05
import logging
import threading
from collections import deque
from typing import Literal

logger = logging.getLogger(__name__)

_EXTRACTION_EMPTY_CONSECUTIVE: int = 3
_HTTP_WALL_CONSECUTIVE: int = 5
_TEXT_THRESHOLD: int = 100
_HTML_THRESHOLD: int = 5000


class CircuitOpenError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: Literal["extraction_empty", "http_wall"],
        threshold: int,
        count: int,
        recent: list[dict],
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.threshold = threshold
        self.count = count
        self.recent = recent


class Breaker:
    def __init__(
        self,
        kind: Literal["extraction_empty", "http_wall"],
        threshold: int,
    ) -> None:
        self.kind = kind
        self.threshold = threshold
        self._count = 0
        self._recent: deque[dict] = deque(maxlen=threshold)
        self._lock = threading.Lock()

    def record_failure(self, **entry_fields: object) -> None:
        with self._lock:
            self._count += 1
            self._recent.append(dict(entry_fields))
            if self._count >= self.threshold:
                recent_snapshot = list(self._recent)
                count = self._count
        # Raise AFTER lock release (D-04)
        if count >= self.threshold:
            raise CircuitOpenError(
                ...,  # kind-specific message (D-06)
                kind=self.kind,
                threshold=self.threshold,
                count=count,
                recent=recent_snapshot,
            )
        logger.warning("breaker %s: failure %d/%d", self.kind, self._count, self.threshold)

    def record_success(self) -> None:
        with self._lock:
            self._count = 0
            self._recent.clear()
```

**Note:** The planner must resolve the lock-release-then-raise pattern carefully — the `count >= threshold` check must use a local variable captured inside the lock, then the raise happens outside. See the "Lock Release Before Raise" pitfall below.

### Pattern 2: D-14 Control Flow — Nested Try (CRITICAL)

D-14's prose says `CircuitOpenError` raised inside the broad `except Exception` body "flies past the broad except" and is "caught by D-12 #2" (`except CircuitOpenError`). This is **partially wrong about the mechanism** — Python does NOT re-match sibling `except` clauses for exceptions raised inside a handler body. [VERIFIED: Python 3.14 live test below]

**Verified Python semantics:**

```python
# Source: Python 3.14 live execution, 2026-05-05 (applicable to 3.11+)
try:
    raise RuntimeError("original")
except CircuitOpenError:
    # NEVER reached for exceptions raised in except Exception body below
    ...
except Exception as e:
    raise CircuitOpenError("from inside broad except")
    # CircuitOpenError propagates PAST this entire try block to the caller
    # It does NOT go back up to 'except CircuitOpenError' above
```

**The correct implementation uses a nested try inside the broad except body:**

```python
# Source: verified pattern, 2026-05-05
except Exception as e:
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
        manifest.set_part_status(story.story_id, part.part_id, "gone",
                                 last_error="HTTP 404 — part removed")
        emit("part.failed", {"part_id": part.part_id, "status_code": 404, "reason": "gone"})
        continue
    elif isinstance(e, httpx.HTTPStatusError) and e.response.status_code != 404:
        try:                                          # <-- nested try
            http_wall.record_failure(
                status_code=e.response.status_code,
                url=str(e.request.url),
            )
        except CircuitOpenError as ce:               # <-- catches record_failure raise
            emit("breaker.opened", {
                "breaker": ce.kind,
                "threshold": ce.threshold,
                "count": ce.count,
                "recent": ce.recent,
            })
            raise ce                                 # propagates out of entire per-part try
    logger.exception("part %s failed: %s", part.part_id, e)
    manifest.set_part_status(story.story_id, part.part_id, "failed", last_error=str(e))
    emit("part.failed", {"part_id": part.part_id, "error": str(e)})
```

This is equivalent to D-12's intent — `breaker.opened` is emitted and `CircuitOpenError` propagates — but implemented correctly. The sibling `except CircuitOpenError` in the per-part block only needs to handle `extraction_empty` breaker trips (which are raised directly in the try body, not from inside an except handler).

**Revised per-part except chain:**

```python
try:
    raw_html = deps.fetch_chapter_html(client, part.url)
    content = deps.parse_chapter(raw_html)
    # --- extraction-empty check here ---
    # extraction_empty.record_failure(...) raises CircuitOpenError directly in try body
    # So it IS caught by except CircuitOpenError below
    inline = ...
    end = ...
    store.write_part_files(...)
    manifest.set_part_status(..., "done", ...)
    emit("part.done", ...)
except AuthFailedError as e:
    manifest.set_part_status(..., "failed", last_error=str(e))
    emit("auth.failed", {...})
    raise
except CircuitOpenError as ce:
    # Catches extraction_empty trip (raised directly in try body above).
    # Does NOT catch http_wall trip (raised inside broad except body — see nested try above).
    emit("breaker.opened", {"breaker": ce.kind, ...})
    raise
except Exception as e:
    # ... 404 routing, nested try for http_wall.record_failure ...
```

[VERIFIED: Python live execution confirmed on 2026-05-05]

### Pattern 3: Extraction-Empty Heuristic Placement

The heuristic check (text < 100 AND html > 5000) runs AFTER `deps.parse_chapter(raw_html)` returns and BEFORE `store.write_part_files()`. This ensures:
- Raw HTML is available (needed for D-09 raw_html.html write)
- Sanitized text is final (Phase 1 nh3 sanitization already applied inside `extract_chapter`)
- Files are NOT written for extraction-empty parts except `raw_html.html`

```python
# In the try body, after parse_chapter:
content = deps.parse_chapter(raw_html)
text_len = len(content.text)
html_len = len(raw_html)
if text_len < _TEXT_THRESHOLD and html_len > _HTML_THRESHOLD:  # import from circuit_breakers
    extraction_empty.record_failure(
        part_id=part.part_id,
        ordinal=part.ordinal,
        text_len=text_len,
        html_len=html_len,
    )
    # record_failure raises CircuitOpenError on 3rd consecutive — execution stops here.
    # For non-trip failures: fall through to write raw_html only, set body_text_failed.
    store.write_raw_html(cfg.output_dir, story, part, raw_html)  # D-09
    manifest.set_part_status(
        story.story_id, part.part_id, "body_text_failed",
        last_error=f"extraction empty: text={text_len} chars, html={html_len} bytes",
    )
    emit("part.failed", {"part_id": part.part_id, "error": f"extraction empty: ..."})
    continue
else:
    extraction_empty.record_success()
    # ... normal write_part_files path ...
```

**Note:** The `continue` after non-trip extraction-empty writes skips comment fetching. Comments are not fetched for empty-body parts (no meaningful content to anchor them).

### Pattern 4: `write_part_files()` Modification for D-09

**Verified signature** at `archive/store.py:107-137`:

```python
def write_part_files(
    output_dir: Path,
    story: Story,
    part: Part,
    content: ChapterContent,
    raw_html: str,
    inline_comments: list[Comment],
    end_comments: list[Comment],
) -> None:
```

The function currently writes: `.json`, `.html` (raw_html), `.txt`, `_comments-inline.json`, `_comments-end.json`. [VERIFIED: store.py read in this session]

**For D-09:** The `raw_html_only=True` kwarg approach is simpler for callers. Alternatively, extract a `write_raw_html(output_dir, story, part, raw_html)` helper. The planner picks based on whether the caller site in `jobs.py` is cleaner. Either way, the file written for extraction-empty is named consistently with the existing `{base}.html` pattern (which already writes raw_html) — so the extraction-empty path writes `{base}.html` only.

**Recommended split** (keeps `archive_story` readable):

```python
# In store.py — add before write_part_files:
def write_raw_html(output_dir: Path, story: Story, part: Part, raw_html: str) -> None:
    """Write only the raw HTML for an extraction-empty part (D-09)."""
    parts_dir = story_dir(output_dir, story) / "parts"
    base = _part_basename(part)
    atomic_write_text(parts_dir / f"{base}.html", raw_html)
```

### Anti-Patterns to Avoid

- **Holding the lock while raising:** `CircuitOpenError` must be raised AFTER the lock is released. Use a local variable to capture `count` and `recent_snapshot` inside the lock, check the local outside. [VERIFIED: TokenBucket pattern in client.py:34-47 releases lock before sleeping]
- **Importing httpx inside Breaker:** Keep `circuit_breakers.py` free of httpx imports. Accept `(status_code: int, url: str)` primitives in `record_failure` — the caller in `jobs.py` already has the `httpx.HTTPStatusError` and can extract these fields.
- **Checking `e.response.status_code` without isinstance guard:** `except Exception` catches all exceptions including `httpx.RequestError` (no `.response` attribute). Always `isinstance(e, httpx.HTTPStatusError)` first.
- **Resetting http_wall on 404:** 404 is explicitly not a failure for the http_wall breaker. The `continue` after 404 handling must NOT call `http_wall.record_success()`.
- **Recording http_wall failure before 401/403 auth check:** `client.py` already raises `AuthFailedError` for 401/403 BEFORE `raise_for_status()`, so those never appear as `httpx.HTTPStatusError` in the broad except. The http_wall counter will never see a 401 or 403. [VERIFIED: client.py:80-91]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Thread-safe bounded queue | Custom linked list | `collections.deque(maxlen=N)` | stdlib, correct, auto-evicts |
| Mutex | `time.sleep` polling | `threading.Lock` | stdlib, established codebase pattern |
| HTTP status inspection | String parsing of exception message | `httpx.HTTPStatusError.response.status_code` (int) | Type-safe, already used in `client.py` |
| Exception chaining confusion | Catching and re-raising with `raise e` | bare `raise` (or `raise ce` for renamed variable) | Preserves `__cause__` chain |

---

## Verified Codebase Facts

### `jobs.py` Per-Part Try/Except — Exact Lines

[VERIFIED: jobs.py read in this session]

| Line Range | Content |
|------------|---------|
| 66–74 | `archive_story()` signature |
| 75–76 | `deps` / `emit` setup |
| 77–100 | Story fetch, `story.start` emit, `upsert_story`, cover fetch |
| 101–105 | Per-part loop start, `done`-skip guard |
| 106–113 | `part.start` emit, `set_part_status("in_progress")` |
| 115 | `try:` — start of per-part try block |
| 116 | `raw_html = deps.fetch_chapter_html(client, part.url)` |
| 117 | `content = deps.parse_chapter(raw_html)` |
| 118–127 | `fetch_inline_comments`, `fetch_end_comments`, `write_part_files` |
| 129–135 | `body_hash`, `set_part_status("done", body_hash=...)` |
| 136–144 | `emit("part.done", ...)` |
| 145–164 | `except AuthFailedError as e:` — emit + raise (Phase 2) |
| 165–173 | `except Exception as e:` — logger.exception + set_part_status("failed") + emit("part.failed") |
| 175–206 | Render loop + `story.done` emit + `RenderError` raise |

**Phase 3 inserts:**
1. Two `Breaker` instantiations after line 76 (after `emit = ...`)
2. Extraction-empty heuristic check + record_failure/record_success between lines 117–120
3. `except CircuitOpenError as ce:` clause between lines 164 and 165
4. HTTP-wall logic (nested try) inside the broad `except Exception` body at line 165

### `auth.py` Precedent — Confirmed Match to CONTEXT.md Claims

[VERIFIED: auth.py read in this session]

| CONTEXT.md Claim | Verified |
|------------------|----------|
| D-23 (Phase 2): "small standalone module with one class + custom exception + lowercase module constants" | `_PROBE_URL` constant, `AuthError(Exception)`, `AuthFailedError(AuthError)` — confirmed |
| D-16 (Phase 2): `except AuthFailedError: emit("auth.failed", {...}); raise` before broad except | Lines 145–164 in `jobs.py` — confirmed |
| `AuthFailedError.__init__(message, *, status_code, url)` keyword-only args | Confirmed at `auth.py:31` |
| `emit("auth.failed", {"part_id": ..., "status_code": ..., "url": ..., "message": ...})` | Confirmed at `jobs.py:158-163` |

### `archive/state.py` — Manifest Facts

[VERIFIED: state.py read in this session]

| Fact | Line | Implication |
|------|------|-------------|
| `set_part_status(story_id, part_id, status, *, body_hash=None, last_error=None)` | 110–128 | D-10 last_error plugs directly in |
| `pending_parts_for()` excludes `('done', 'gone', 'private')` | 150–158 | `body_text_failed` rows retry on next run — correct per D-08 |
| `PartStatus = Literal[..., "body_text_failed", "gone", ...]` | `models.py:4` | Both statuses pre-exist; no schema migration |

### `client.py` HTTP Behavior — Confirmed for D-16

[VERIFIED: client.py read in this session]

| Behavior | Lines | Impact |
|----------|-------|--------|
| 401/403 raises `AuthFailedError` BEFORE `raise_for_status()` | 80–91 | Auth failures never reach http_wall counter |
| 400 + PermissionDenied raises `AuthFailedError` | 99–118 | Same — never reaches http_wall |
| 429 retries (up to `max_attempts=5`), then falls through to `raise_for_status()` | 120–125, 133–135 | Final 429 outcome = one HTTPStatusError = one http_wall increment (D-16 confirmed) |
| 5xx retries similarly | 126–129 | Final 5xx = one increment |
| `httpx.RequestError` stored in `last_exc`, re-raised if all attempts fail | 65–68, 132–133 | Transport errors are `RequestError`, not `HTTPStatusError` — never hit http_wall (D-15 confirmed) |

---

## Common Pitfalls

### Pitfall 1: Lock Held During Exception Raise

**What goes wrong:** If `CircuitOpenError` is raised while holding `self._lock`, exception unwind does not release the lock (Python's `with` statement does release on exception within the `with` block, but the raise happens after `with` exits in the pattern above — this needs careful structuring).

**Why it happens:** Naively writing `raise CircuitOpenError(...)` inside the `with self._lock:` block holds the lock during exception propagation until the `with` block exits via its `__exit__`. Actually Python's `with` statement DOES call `__exit__` on exception, so `threading.Lock` IS released. However, the established codebase pattern (D-04) explicitly releases before raising for clarity and to match `TokenBucket`'s pattern.

**How to avoid:** Capture `count` and `recent_snapshot` locals inside the `with` block. After the `with` block, check locals and raise. [VERIFIED: TokenBucket uses `return` not `raise` inside the lock; established precedent]

```python
# Correct pattern:
with self._lock:
    self._count += 1
    self._recent.append(entry)
    count = self._count
    recent_snapshot = list(self._recent) if self._count >= self.threshold else []
# Lock released here:
if count >= self.threshold:
    raise CircuitOpenError(..., count=count, recent=recent_snapshot)
logger.warning("...")
```

### Pitfall 2: D-14 Control Flow Misimplementation

**What goes wrong:** Planner implements `except CircuitOpenError` as a sibling clause to `except Exception` and expects it to catch `CircuitOpenError` raised by `http_wall.record_failure()` inside `except Exception`. This does NOT work. [VERIFIED: Python 3.14 live execution, 2026-05-05]

**Why it happens:** D-14's prose says the breaker's error "flies past the broad except", implying it gets caught by the sibling `except CircuitOpenError`. Python's exception dispatch does not re-scan sibling clauses for exceptions raised inside a handler body.

**How to avoid:** Use a nested `try/except CircuitOpenError` inside the broad `except Exception` body to catch `http_wall.record_failure()`'s raise, emit `breaker.opened`, and re-raise. (See Pattern 2 above for the verified code.)

**Warning signs:** If unit tests show `breaker.opened` is never emitted for http_wall trips, this pitfall is the cause.

### Pitfall 3: Missing isinstance Guard in Broad Except

**What goes wrong:** Accessing `e.response.status_code` when `e` is an `httpx.RequestError` (connection timeout, DNS failure) causes `AttributeError` because `RequestError` has no `.response` attribute.

**How to avoid:**

```python
except Exception as e:
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 404:
            ...
        else:
            ...
    # other Exception handling
```

[VERIFIED: client.py:65 shows RequestError is caught separately; jobs.py broad except currently handles any Exception — Phase 3 adds the isinstance check]

### Pitfall 4: Forgetting Comment Fetch Skip for body_text_failed Parts

**What goes wrong:** After detecting extraction-empty, the code falls through to `fetch_inline_comments` and `fetch_end_comments`, making API calls for a part known to have no meaningful content.

**How to avoid:** Use `continue` after writing raw_html and setting `body_text_failed` status to skip the rest of the part processing (including comment fetching and the normal `write_part_files` call).

### Pitfall 5: Monkeypatch Target for Constants

**What goes wrong:** Test monkeypatches `_EXTRACTION_EMPTY_CONSECUTIVE` on the `circuit_breakers` module, but `jobs.py` already imported it as a local name — the monkey-patch doesn't affect the Breaker instance created from the imported constant.

**How to avoid:** The Breaker is instantiated with the constant's VALUE at call time:

```python
# In jobs.py:
import wattpad_crawler.circuit_breakers as cb
...
extraction_empty = cb.Breaker("extraction_empty", cb._EXTRACTION_EMPTY_CONSECUTIVE)
```

Tests monkeypatch `cb._EXTRACTION_EMPTY_CONSECUTIVE` AND mock the `Breaker` constructor call (or use `cb.Breaker` directly so the threshold comes from the module attribute at instantiation). The cleaner approach: `jobs.py` reads the constant at Breaker-instantiation time, so monkeypatching the module attribute before calling `archive_story()` works correctly.

**The Phase 1 precedent** (D-11 / D-02): test monkeypatching the `_MAX_*` constants in the module that READS them at use-time. Phase 3 follows: monkeypatch `wattpad_crawler.circuit_breakers._EXTRACTION_EMPTY_CONSECUTIVE = 2` before calling `archive_story()`. [ASSUMED based on Phase 1 pattern; verify in test_circuit_breakers.py implementation]

---

## Code Examples

### Isolation Test Pattern (test_circuit_breakers.py)

```python
# Source: derived from auth.py test pattern and D-04 spec, 2026-05-05
import threading
import pytest
from wattpad_crawler.circuit_breakers import Breaker, CircuitOpenError

def test_breaker_does_not_trip_below_threshold():
    b = Breaker("http_wall", threshold=3)
    b.record_failure(status_code=503, url="https://w/1")
    b.record_failure(status_code=503, url="https://w/2")
    # No raise — threshold is 3, only 2 failures

def test_breaker_trips_on_threshold():
    b = Breaker("http_wall", threshold=3)
    b.record_failure(status_code=503, url="https://w/1")
    b.record_failure(status_code=503, url="https://w/2")
    with pytest.raises(CircuitOpenError) as exc_info:
        b.record_failure(status_code=503, url="https://w/3")
    e = exc_info.value
    assert e.kind == "http_wall"
    assert e.threshold == 3
    assert e.count == 3
    assert len(e.recent) == 3

def test_breaker_record_success_resets_counter():
    b = Breaker("http_wall", threshold=2)
    b.record_failure(status_code=503, url="https://w/1")
    b.record_success()
    # Should not raise — counter reset
    b.record_failure(status_code=503, url="https://w/2")

def test_breaker_thread_safety():
    """Two threads racing record_failure — exactly one raises CircuitOpenError."""
    b = Breaker("http_wall", threshold=2)
    results = []
    def worker():
        try:
            b.record_failure(status_code=503, url="https://w/x")
            results.append("ok")
        except CircuitOpenError:
            results.append("trip")
    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert results.count("trip") == 1
    assert results.count("ok") == 1
```

### Integration Test Pattern (test_jobs.py additions)

```python
# Source: existing test_jobs.py _make_deps pattern, 2026-05-05
import wattpad_crawler.circuit_breakers as cb

def test_extraction_empty_breaker_trips_after_3(output_dir, monkeypatch):
    # Monkeypatch threshold to 2 for small test story
    monkeypatch.setattr(cb, "_EXTRACTION_EMPTY_CONSECUTIVE", 2)
    monkeypatch.setattr(cb, "_HTML_THRESHOLD", 10)  # low threshold for test HTML

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(story_id="42", title="Hi", author_username="bob", parts=[
        Part(part_id="100", ordinal=1, title="One", url="https://w/100"),
        Part(part_id="101", ordinal=2, title="Two", url="https://w/101"),
        Part(part_id="102", ordinal=3, title="Three", url="https://w/102"),
    ])
    fake_client = MagicMock()
    deps = _make_deps(story)
    # parse_chapter returns empty text, fetch_chapter_html returns >10 chars
    deps.parse_chapter = MagicMock(return_value=ChapterContent(text="", paragraphs=[], images=[]))
    deps.fetch_chapter_html = MagicMock(return_value="<html>" + "x" * 100 + "</html>")
    events = []

    with pytest.raises(CircuitOpenError):
        archive_story(cfg, fake_client, manifest, "42",
                      deps=deps, progress=lambda k, d: events.append((k, d)))

    breaker_events = [d for k, d in events if k == "breaker.opened"]
    assert len(breaker_events) == 1
    assert breaker_events[0]["breaker"] == "extraction_empty"
    # Parts 1 and 2 should be body_text_failed
    for pid in ["100", "101"]:
        row = manifest.get_part("42", pid)
        assert row["status"] == "body_text_failed"
        assert "extraction empty" in row["last_error"]
    manifest.close()
```

### HTTP MockTransport Pattern for http_wall Tests

```python
# Source: test_auth.py make_client pattern, adapted 2026-05-05
import httpx

def make_503_transport(n_failures: int):
    count = {"val": 0}
    def handler(req):
        count["val"] += 1
        if count["val"] <= n_failures:
            return httpx.Response(503)
        return httpx.Response(200, text="<html></html>")
    return httpx.MockTransport(handler)
```

---

## Runtime State Inventory

Step 2.5 SKIPPED — Phase 3 is a greenfield module addition + localized edits to `jobs.py` and `store.py`. No rename, refactor, or migration. No stored data strings change. No runtime state affected.

---

## Environment Availability

Step 2.6: External dependencies are stdlib + already-installed packages only.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | `circuit_breakers.py` (pipe unions) | Yes | 3.14 (dev machine) | — |
| `threading` | Breaker lock | Yes (stdlib) | — | — |
| `collections.deque` | recent-tape | Yes (stdlib) | — | — |
| `httpx` | isinstance check in jobs.py | Yes (>=0.27 installed) | Installed | — |
| `pytest` | Tests | Yes (>=8.0) | Installed | — |

No missing dependencies. [VERIFIED: pyproject.toml + running test suite 249 passed]

---

## Validation Architecture

`workflow.nyquist_validation` is `true` in `.planning/config.json` — section required.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/unit/test_circuit_breakers.py tests/unit/test_jobs.py -x -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RES-01 | Extraction-empty breaker trips after exactly 3; not after 2 | unit | `pytest tests/unit/test_circuit_breakers.py -k "extraction_empty" -x` | Wave 0 |
| RES-01 | `body_text_failed` status in manifest; `last_error` matches D-10 format | integration (archive_story) | `pytest tests/unit/test_jobs.py -k "extraction_empty" -x` | Wave 0 |
| RES-01 | `raw_html.html` written; other files (json/txt/comments) NOT written | integration | `pytest tests/unit/test_jobs.py -k "raw_html_only" -x` | Wave 0 |
| RES-02 | HTTP-wall trips after exactly 5; not after 4; 404 does not increment | unit | `pytest tests/unit/test_circuit_breakers.py -k "http_wall" -x` | Wave 0 |
| RES-02 | 404 mid-stream → `"gone"` status, http_wall counter unchanged | integration | `pytest tests/unit/test_jobs.py -k "404_gone" -x` | Wave 0 |
| RES-02 | 5 consecutive 503s → `CircuitOpenError` raised with http_wall message | integration | `pytest tests/unit/test_jobs.py -k "http_wall_trips" -x` | Wave 0 |
| RES-03 | `breaker.opened` SSE event in event stream with correct payload schema | integration | `pytest tests/unit/test_jobs.py -k "breaker_opened_event" -x` | Wave 0 |
| RES-03 | `JobRunner` routes `CircuitOpenError` to `set_failed` (existing behavior) | integration | `pytest tests/unit/test_runner.py -k "circuit" -x` | Wave 0 |

### False-Pass Risks

| Test | False-Pass Scenario | Prevention |
|------|---------------------|------------|
| "breaker does not fire on 2" | Test story has only 2 parts, breaker fires on 2nd part (which is the Nth for threshold=3 if monkeypatched wrong) | Monkeypatch `_EXTRACTION_EMPTY_CONSECUTIVE=2` explicitly and test with exactly N-1 failures first |
| "404 does not increment counter" | Test only checks part status; http_wall counter state not inspected | Assert Breaker's internal `_count` is 0 after 404, OR prove 4 subsequent 4xx+1 404 does NOT trip |
| D-14 control flow (breaker.opened emitted) | Nested try not implemented; `breaker.opened` never emitted for http_wall | Assert `breaker.opened` event is present AND has `breaker == "http_wall"` |
| Thread-safety test | Race not actually racing (GIL timing) | Use `threading.Barrier` to synchronize thread entry |

### Monkeypatch Strategy for Constants

All four constants live in `wattpad_crawler.circuit_breakers`. Tests use `monkeypatch.setattr(cb, "_EXTRACTION_EMPTY_CONSECUTIVE", 2)` BEFORE calling `archive_story()`. `jobs.py` reads the constant at Breaker-instantiation time (call to `cb.Breaker("extraction_empty", cb._EXTRACTION_EMPTY_CONSECUTIVE)`), so the monkeypatch is effective.

For Breaker isolation tests that don't touch `jobs.py`, pass `threshold=N` directly to `Breaker(kind, threshold=N)` — no monkeypatching needed.

### Sampling Rate

- **Per task commit:** `python -m pytest tests/unit/test_circuit_breakers.py -x -q`
- **Per wave merge:** `python -m pytest tests/unit/ -q`
- **Phase gate:** `python -m pytest tests/ -q` (all 249+ tests green)

### Wave 0 Gaps

- [ ] `tests/unit/test_circuit_breakers.py` — Breaker isolation tests (covers RES-01, RES-02, RES-03 unit)
- [ ] Additions to `tests/unit/test_jobs.py` — integration tests for both breakers (covers RES-01, RES-02, RES-03 integration)
- [ ] Optional: `tests/unit/test_runner.py` — confirm CircuitOpenError routes to `set_failed` (may already pass without changes if JobRunner's existing top-level `except Exception` catches it)

---

## Security Domain

No new network access, no authentication changes, no user input, no cryptography. Phase 3 is internal logic only. ASVS categories not applicable to this phase's changes.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Monkeypatching `cb._EXTRACTION_EMPTY_CONSECUTIVE` before calling `archive_story()` is effective because Breaker is instantiated at call time using `cb._EXTRACTION_EMPTY_CONSECUTIVE` | Pitfall 5 / Validation | If planner uses a local import of the constant (`from wattpad_crawler.circuit_breakers import _EXTRACTION_EMPTY_CONSECUTIVE`), monkeypatch hits wrong name; planner must use `cb._EXTRACTION_EMPTY_CONSECUTIVE` via module reference |
| A2 | `test_runner.py` tests will pass without changes because `JobRunner._run` has an existing `except Exception → set_failed` that catches `CircuitOpenError` | Validation Architecture | If `JobRunner._run` catches only specific exception types, a new test or code change may be needed |

---

## Open Questions

1. **Extraction-empty: should comments be fetched?**
   - What we know: D-09 says "skip JSON/text/comment files" for extraction-empty parts; the code currently fetches comments before `write_part_files`
   - What's unclear: D-09 implies no comment files written, but doesn't say whether to skip the API calls
   - Recommendation: Skip comment API calls (via `continue` before `fetch_inline_comments`) — no point fetching comments for a part known to have no body text; fewer API calls reduces the extraction-empty signature. Not locked in CONTEXT.md so planner decides.

2. **`write_part_files()` split vs kwarg — exact filename for raw HTML in extraction-empty case**
   - What we know: `write_part_files()` currently writes `{base}.html` for raw_html; D-09 says write `raw_html.html`
   - What's unclear: Is the extraction-empty file named `raw_html.html` (a literal filename) or `{base}.html` (same as normal)?
   - Recommendation: Use the same `{base}.html` naming as normal — it's consistent with the existing archive layout and the user can identify extraction-empty parts by their `body_text_failed` manifest status. "raw_html.html" in CONTEXT.md D-09 is describing the content (raw HTML), not prescribing a filename.

---

## Sources

### Primary (HIGH confidence)

- `wattpad_crawler/jobs.py` — read in full; per-part try/except structure at lines 115–174 verified; exact lines documented
- `wattpad_crawler/auth.py` — read in full; precedent module shape confirmed; `AuthFailedError` constructor confirmed
- `wattpad_crawler/archive/store.py` — read in full; `write_part_files()` signature confirmed; `atomic_write_text` pattern confirmed
- `wattpad_crawler/archive/state.py` — read in full; `set_part_status()` signature confirmed; `pending_parts_for()` exclusion list confirmed
- `wattpad_crawler/models.py` — read in full; `PartStatus` literals confirmed including `body_text_failed` and `gone`
- `wattpad_crawler/client.py` — read in full; 429/5xx retry behavior confirmed; `AuthFailedError` raising before `raise_for_status()` confirmed
- `wattpad_crawler/scrape/chapter_html.py` — read in full; `ChapterContent.text` field confirmed as sanitized text
- `tests/unit/test_jobs.py` — read in full; `_make_deps()` helper, `output_dir` fixture usage, monkeypatch pattern confirmed
- `tests/conftest.py` — read in full; `output_dir` fixture confirmed
- Python 3.14 live execution — D-14 control flow verified: exceptions raised inside `except` body do NOT get caught by sibling `except` clauses of the same `try` block

### Secondary (MEDIUM confidence)

- `pyproject.toml` — pytest configuration, dependency versions, ruff settings confirmed
- `.planning/config.json` — `nyquist_validation: true` confirmed

### Tertiary (LOW confidence — none)

All claims in this research were verified from codebase or live Python execution.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — stdlib only; no new packages; all existing modules verified by direct read
- Architecture: HIGH — control flow verified by live Python execution; all integration points confirmed by reading source
- Pitfalls: HIGH — D-14 pitfall verified by live execution; all other pitfalls derived from verified code reads
- Test infrastructure: HIGH — existing test patterns read directly; framework confirmed running (249 passed)

**Research date:** 2026-05-05
**Valid until:** 2026-07-05 (stdlib patterns are stable; httpx exception hierarchy stable in 0.27+)
