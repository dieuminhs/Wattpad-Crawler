---
phase: 01-local-hardening-fixes
reviewed: 2026-05-03T00:00:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - pyproject.toml
  - local_story_archive/api/comments.py
  - local_story_archive/jobs.py
  - local_story_archive/scrape/chapter_html.py
  - local_story_archive/web/routes.py
  - local_story_archive/web/runner.py
  - local_story_archive/web/templates/job.html
  - tests/unit/test_api_comments.py
  - tests/unit/test_chapter_html.py
  - tests/unit/test_jobs.py
  - tests/unit/test_runner.py
  - tests/unit/test_web_routes.py
findings:
  critical: 0
  warning: 2
  info: 5
  total: 7
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-05-03
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Phase 1 cleanly delivers all six hardening requirements (REL-01..04, SAN-01..02) with thorough test coverage and documentation that traces every decision back to CONTEXT.md (D-01..D-18) and RESEARCH.md. The implementations follow the project's established conventions: pipe-syntax unions, `_MAX_*` private constants matching the existing `_MAX_PAGES` precedent, custom exceptions inheriting directly from `Exception`, `pathlib.Path` everywhere, and a single new runtime dependency (`nh3>=0.3,<0.4`) added to the canonical `pyproject.toml`. Tests exhaustively cover the 1100-event eviction, 60-job pruning preserves running jobs, 30-level recursion no `RecursionError`, all-three-renderers-fail `RenderError`, and the eviction-gap synthetic SSE event.

No critical security or correctness issues were identified. Two warnings cover (1) an unsafe `innerHTML` interpolation pattern in the SSE consumer JS that, while currently safe given controlled `kind` values, is a fragile practice for any future emit kind containing `<`, and (2) an inconsistent constant reference in the truncation log message that breaks when callers pass a custom `max_depth`. Five info items cover stylistic/maintainability nits: forward-reference of `RenderError` from `archive_story` before its definition, function-local `import` statements that violate the project's module-level-imports convention without falling under the documented `web/routes.py` async-context-manager exception, a Jinja2 `{{ ev.data }}` block that renders Python `dict.__str__` output rather than JSON, and two minor test/doc gaps.

## Warnings

### WR-01: SSE consumer interpolates `data.kind` directly into `innerHTML`

**File:** `local_story_archive/web/templates/job.html:40`
**Issue:** The inline JS event handler builds new event-log entries with `div.innerHTML = '<code>' + data.kind + '</code> ' + JSON.stringify(data.data);`. `data.kind` is concatenated into HTML without escaping. Currently safe only because every `kind` value emitted by `archive_story`, `archive_many`, the JobRunner, and the SSE handler itself is a hard-coded literal (`"part.start"`, `"story.done"`, `"render.failed"`, `"events.evicted"`, `"__status__"`, etc.) with no user-controlled component. The first emit kind that ever interpolates a value (e.g. a future `f"render.{name}.failed"` pattern using user input, or a debug emit kind containing chapter titles) becomes a stored XSS vector for any browser that loads `/jobs/{job_id}`. Single-user tool with self-controlled input keeps the actual risk low, but the pattern is fragile and inconsistent with the server-side template's auto-escaped `{{ ev.kind }}` (line 22).
**Fix:** Use `textContent` for the kind label (it's a plain identifier, not HTML), or build the children with `document.createElement` + `Node.textContent`:
```javascript
es.onmessage = function (e) {
  var data = JSON.parse(e.data);
  if (data.kind === '__status__') {
    es.close();
    setTimeout(function () { location.reload(); }, 500);
    return;
  }
  var div = document.createElement('div');
  div.className = 'ev';
  var code = document.createElement('code');
  code.textContent = data.kind;             // safe — sets text, not HTML
  div.appendChild(code);
  div.appendChild(document.createTextNode(' ' + JSON.stringify(data.data)));
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
};
```

### WR-02: Truncation warning hard-codes `_MAX_COMMENT_DEPTH` instead of the actual `max_depth` used

**File:** `local_story_archive/api/comments.py:88-92`
**Issue:** `parse_comments_page` calls `_parse_one(r)` with no explicit `max_depth`, so `_parse_one` falls through to the module default of `_MAX_COMMENT_DEPTH = 10`. The warning emitted on truncation (lines 88-92) hard-codes `_MAX_COMMENT_DEPTH` in the format args:
```python
logger.warning(
    "comment %s truncated: replies beyond depth %d dropped",
    comment.comment_id,
    _MAX_COMMENT_DEPTH,
)
```
Today this is correct because `parse_comments_page` never passes `max_depth`, so the module constant is the actual cap. But the contract documented in `test_parse_one_monkeypatch_constant_changes_behavior` says `_parse_one` accepts an explicit `max_depth` for testability (D-11/D-12). Any future caller (or a test that calls `parse_comments_page` after monkeypatching the module constant *and* expects the warning to reflect the new value) gets a misleading log line. Since the actual cap used is not threaded back from `_parse_one`, the warning may report the wrong number even today if `_MAX_COMMENT_DEPTH` is monkeypatched at module level after the function default has captured the original value.
**Fix:** Either (a) thread the effective `max_depth` out of `_parse_one` (third tuple element, or via a small dataclass result), or (b) read the module constant at warning-emit time which is what the current code does, and add a docstring note clarifying that `parse_comments_page` always uses the module default. Option (b) is the smaller change and matches today's reality:
```python
# In parse_comments_page, replace:
_MAX_COMMENT_DEPTH,
# with an explicit reference that resolves at call time and is documented:
api_comments_module._MAX_COMMENT_DEPTH,  # or just leave the bare name

# Add a one-line docstring to parse_comments_page:
"""Parse a comments page. Always uses the module-level _MAX_COMMENT_DEPTH;
custom max_depth is only available via direct _parse_one calls (tests)."""
```
The current code already does (b) effectively (bare-name reference is resolved at function-call time, picking up monkeypatches), so a docstring sentence may be the only change needed. If the planner intends `parse_comments_page` to ever honor a per-call cap, switch to option (a).

## Info

### IN-01: `RenderError` is referenced before its definition in the same module

**File:** `local_story_archive/jobs.py:185, 192`
**Issue:** `archive_story` raises `RenderError` at line 185 but `class RenderError(Exception):` is defined at line 192, after the function body. Python resolves `RenderError` at raise-time so this works correctly, but it's awkward to read top-down: the exception name appears as an unresolved reference until the bottom of the file. The sibling `ResolveError` (line 188) follows the same pattern and is also referenced earlier than its definition (line 223 — `resolve_story_id`). This is consistent with the existing style, so the cleanest fix is just to acknowledge the pattern; a stricter alternative is to hoist both exception classes above `archive_story`.
**Fix:** Optional. Move `class ResolveError(Exception):` and `class RenderError(Exception):` above `archive_story()` for readability. No behavioral change.

### IN-02: Function-local `import` statements in `web/routes.py` violate the convention exception scope

**File:** `local_story_archive/web/routes.py:73, 179, 180`
**Issue:** Three function-local imports exist:
- Line 73: `from local_story_archive.config import load_config` inside `setup_post`
- Line 179: `import asyncio` inside `event_gen`
- Line 180: `import time as _time` inside `event_gen`

CLAUDE.md states: *"Imports at module level, not inside functions (except async context manager imports in web/routes.py)"*. None of these three are async context manager imports. `asyncio` and `time` are stdlib modules safe to import at top level; `load_config` is already imported indirectly elsewhere. The function-local imports add no measurable lazy-load benefit (the module is loaded at app startup anyway) and create inconsistency with the convention.
**Fix:** Hoist all three to the top-level imports block:
```python
import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sse_starlette.sse import EventSourceResponse

from local_story_archive.api.user import fetch_library, fetch_list_story_ids
from local_story_archive.archive.state import Manifest
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import load_config
from local_story_archive.jobs import (
    ResolveError,
    archive_many,
    archive_story,
    resolve_story_id,
)
from local_story_archive.web.library_browser import scan_library
```
Then drop the `import asyncio`, `import time as _time`, and `from local_story_archive.config import load_config` lines from inside the functions. Replace `_time.time()` with `time.time()`.

### IN-03: Server-rendered event log shows `dict.__str__()` instead of JSON

**File:** `local_story_archive/web/templates/job.html:21-24`
**Issue:** The pre-script section renders existing events as `<code>{{ ev.kind }}</code> {{ ev.data }}` where `{{ ev.data }}` is a Python dict that Jinja2 stringifies via `str(dict)`, producing single-quoted Python repr like `{'part_id': '100', 'ordinal': 1}`. The JS-rendered events (line 40) use `JSON.stringify(data.data)` which produces standard double-quoted JSON. The visible mismatch is cosmetic but confusing on a job that has both server-rendered (existing) and live (newly streamed) events visible at once.
**Fix:** Use a Jinja2 filter to JSON-encode the data field so server-rendered events match the JS rendering:
```html
<div class="ev"><code>{{ ev.kind }}</code> {{ ev.data | tojson }}</div>
```
`tojson` is a Jinja2 built-in (and FastAPI/Starlette already register it). No new imports needed.

### IN-04: `_resolve_story_dir` `cfg` parameter has no type hint

**File:** `local_story_archive/web/routes.py:266`
**Issue:** Project convention per CLAUDE.md is *"Comprehensive type annotations on all functions and dataclass fields"* and *"Return types always specified"*. `_resolve_story_dir(cfg, author: str, dir_name: str) -> Path:` is missing the type for `cfg`, and `_build_work` (line 93) is also missing types on `cfg` (and `args: dict`). Sister helpers like `_save_cookie(output_dir: Path, cookie: str) -> None` correctly annotate every parameter.
**Fix:** Annotate both helpers:
```python
from local_story_archive.config import Config

def _build_work(cfg: Config, kind: str, args: dict[str, Any]) -> Callable[..., None]:
    ...

def _resolve_story_dir(cfg: Config, author: str, dir_name: str) -> Path:
    ...
```
(`Any` from `typing`, `Callable` from `collections.abc` per project convention.)

### IN-05: `IN-05`: `JobRunner.submit` adds to `_running` before `thread.start()`, briefly creating a window where the set says "running" but no work has started

**File:** `local_story_archive/web/runner.py:168-174`
**Issue:** Order of operations in `submit`:
```python
thread = threading.Thread(target=self._run, args=(job, work), name=f"job-{job.job_id}", daemon=True)
with self._lock:
    self._running.add(job.job_id)
thread.start()
```
The job id is added to `_running` while still holding the lock, then `thread.start()` is called outside the lock. `running_count()` will report the job as running before its thread has even been scheduled (and before `Job.set_running()` flips the status). For a personal-use tool this is harmless — `running_count` is a diagnostic, not a correctness invariant. Worth noting because the typical idiom is to start the thread first and let `_run`'s first action register itself, eliminating the gap. Given the existing pattern is symmetrical (`finally: self._running.discard(...)` in `_run`), the current code is internally consistent.
**Fix:** Optional. If exact-time accuracy of `running_count()` matters in a future test, move `self._running.add(job.job_id)` into `_run` directly under the existing try-block (paired with the existing `discard` in `finally`). No change needed for v1.

---

_Reviewed: 2026-05-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
