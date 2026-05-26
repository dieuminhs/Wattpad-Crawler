---
phase: 01-local-hardening-fixes
verified: 2026-05-03T00:00:00Z
status: passed
score: 4/4 ROADMAP success criteria + 6/6 requirements satisfied
overrides_applied: 0
---

# Phase 1: Local Hardening Fixes Verification Report

**Phase Goal:** Close 6 reliability/security gaps in the existing local crawler — HTML sanitization (SAN-01, SAN-02), depth-bounded comment recursion (REL-01), bounded web-layer resources with SSE cursor (REL-02, REL-03), per-format render error handling (REL-04). All work is local, single-user, Python-only.
**Verified:** 2026-05-03
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | A 15-level nested-replies fixture is parsed without RecursionError; the log contains a depth-cap warning and the top-level Comment object is preserved with replies truncated at depth 10 | VERIFIED | End-to-end probe printed `WARNING:local_story_archive.api.comments:comment c15 truncated: replies beyond depth 10 dropped` then `OK: 15-level chain parsed; top-level preserved with replies truncated at depth 10`. Walked exactly 10 levels deep; level-10 leaf has `replies == []`. |
| SC2 | A chapter HTML containing `<img src="...">`, `<br>`, and `data-p-id` is extracted; the stored paragraph `html` contains all three intact after nh3 sanitization | VERIFIED | End-to-end probe output: `hi <img src="a.jpg"><br>x <b data-p-id="in">y</b>`. All three constructs preserved; script/onerror/javascript: stripped (verified by 12 unit tests in `tests/unit/test_chapter_html.py`). |
| SC3 | Submitting 60+ jobs does not grow `JobManager._jobs` beyond the 50-job cap; a long-running Job does not grow past 1000 events | VERIFIED | End-to-end probe: 60 done jobs → `len(mgr._jobs) == 50`. 1100 emits → `len(j.events) == 1000`. Eviction starts at seq 101. |
| SC4 | All-renderers-fail story produces job with `status == "failed"` and a progress event naming which formats failed | VERIFIED | `RenderError(Exception)` raised after `story.done` emit when all 3 renderers fail; `JobRunner._run`'s existing `except Exception` routes to `set_failed(str(e))`. Verified by `test_archive_story_raises_render_error_when_all_renderers_fail` and `test_archive_many_records_render_error_in_results`. |

**Score:** 4/4 ROADMAP success criteria verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | `nh3>=0.3,<0.4` in `[project] dependencies`; no `bleach` | VERIFIED | Line 19 contains `"nh3>=0.3,<0.4",`. Grep for `bleach` in pyproject.toml returns no matches. `import nh3; nh3.Cleaner` available. |
| `local_story_archive/scrape/chapter_html.py` | Module-scope `_PARAGRAPH_CLEANER = nh3.Cleaner(...)` with D-01..D-03 allowlist; `.clean(raw_html)` called in extract loop | VERIFIED | Lines 15-23 construct the Cleaner with `tags={"img","br","b","i","em","strong","u","a"}`, `attributes={"img":{"src","alt"},"a":{"href"},"*":{"data-p-id"}}`, `strip_comments=True`. Line 59 calls `clean_html = _PARAGRAPH_CLEANER.clean(raw_html)`. No `generic_attribute_prefixes` or `url_schemes=` overrides. |
| `local_story_archive/api/comments.py` | `_MAX_COMMENT_DEPTH = 10`; `_parse_one(raw, depth=0, *, max_depth=...) -> tuple[Comment | None, bool]`; `parse_comments_page` emits `logger.warning` per truncated subtree | VERIFIED | Line 16: `_MAX_COMMENT_DEPTH = 10`. Lines 19-24: signature exactly matches plan. Line 42: `if depth >= max_depth:` guard. Lines 88-92: `logger.warning` inside `if was_truncated:` block. Module logger defined line 8. |
| `local_story_archive/web/runner.py` | `_MAX_EVENTS_PER_JOB = 1000`, `_MAX_JOBS = 50`; `Job.events: deque[ProgressEvent]`; `Job.next_seq` PUBLIC; `ProgressEvent.seq`; `Job.snapshot_events(after_seq)`; `Job.oldest_seq()`; `JobManager.create` insert-then-prune; running pinned | VERIFIED | Lines 15-16 declare module caps. Line 30: `seq: int = 0`. Line 45: `events: deque[ProgressEvent] = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB))`. Line 52: `next_seq: int = 0` (PUBLIC, no underscore). Lines 81-89: `snapshot_events(after_seq)` filters by `e.seq > after_seq`. Lines 91-100: `oldest_seq()` returns 0 for empty deque. Lines 115-141: `JobManager.create` inserts first, then prunes only `JobStatus.done/failed`. No `_next_seq` or `after_index` substring anywhere in `local_story_archive/`. |
| `local_story_archive/web/routes.py` | `job_stream(after_seq: int = 0)`; emits synthetic `events.evicted` with `dropped_count`/`requested_after_seq`/`oldest_available_seq` on first-poll gap; per-stream `gap_announced` latch; each real event JSON has `seq` | VERIFIED | Line 164: signature exactly `async def job_stream(request: Request, job_id: str, after_seq: int = 0):`. Line 182: `last_seq = after_seq`. Line 186: `gap_announced = False`. Lines 195-212: gap check with synthetic `events.evicted` payload using exact key names. Line 222: `"seq": ev.seq` in real event JSON. No `?after=` substring or `after: int = 0` in file. |
| `local_story_archive/web/templates/job.html` | EventSource URL uses `?after_seq={{ job.next_seq }}` | VERIFIED | Line 30: `var es = new EventSource("/jobs/{{ job.job_id }}/stream?after_seq={{ job.next_seq }}");`. No `?after=` or `job.events|length` anywhere in `local_story_archive/web/templates/`. |
| `local_story_archive/jobs.py` | `class RenderError(Exception):` adjacent to `ResolveError`; `render_status: dict[str, Literal["ok","failed"]]`; `story.done` emitted BEFORE `raise RenderError`; raise gated on all-failed | VERIFIED | Line 6: `from typing import Literal` imported. Lines 162-174: render loop assigns `render_status[name] = "ok"` / `"failed"` per branch; existing `emit("render.failed", ...)` preserved. Lines 176-182: `emit("story.done", {..., "render_status": render_status})` BEFORE raise. Lines 184-185: `if all(v == "failed" ...): raise RenderError(...)`. Lines 188-189: `class ResolveError(Exception): pass`. Lines 192-203: `class RenderError(Exception):` with docstring. |
| Test files | 5 test files contain the new tests | VERIFIED | `tests/unit/test_chapter_html.py` (19 tests, 12 new), `tests/unit/test_api_comments.py` (11 tests, 10 new), `tests/unit/test_runner.py` (31 tests, 19 new), `tests/unit/test_jobs.py` (20 tests, 6 new), `tests/unit/test_web_routes.py` (31 tests, 7 new). All required test function names from plan acceptance criteria present. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `chapter_html.py:extract_chapter` | `_PARAGRAPH_CLEANER.clean(raw_html)` | Per-paragraph call inside `for para in para_els:` before storage | WIRED | Line 59: `clean_html = _PARAGRAPH_CLEANER.clean(raw_html)`; result assigned to `paragraphs[i]["html"]` at line 64. |
| `comments.py:_parse_one` | `Comment.replies` field with truncation flag | At `depth >= max_depth`, `replies = []` and truncation flag set; otherwise recursive `_parse_one(r, depth+1, max_depth=max_depth)` calls aggregate | WIRED | Lines 42-59: cap branch returns Comment with empty replies + flag; recurse branch propagates `child_trunc`. |
| `comments.py:parse_comments_page` | `logger.warning` | Iterates parsed comments; emits one warning per top-level truncated subtree | WIRED | Lines 84-92: `if was_truncated: logger.warning(...)` with `comment_id` and depth in message. |
| `runner.py:Job.emit` | `Job.events` deque + `ProgressEvent.seq` | `Job._lock`-wrapped: `next_seq += 1`, append new ProgressEvent with assigned seq | WIRED | Lines 56-60: `with self._lock: self.next_seq += 1; self.events.append(ProgressEvent(..., seq=self.next_seq))`. |
| `runner.py:JobManager.create` | `_jobs` dict + `_order` list pruning | Insert-first then iterate `_order` evicting only done/failed | WIRED | Lines 115-141: assignment to `self._jobs[job.job_id]` precedes the `if len(self._jobs) > _MAX_JOBS:` block. Predicate uses `j.status in (JobStatus.done, JobStatus.failed)`. |
| `routes.py:job_stream` | `Job.snapshot_events(after_seq=...)` and `Job.oldest_seq()` | Async generator passes `last_seq` to `snapshot_events`; calls `oldest_seq()` for gap detection | WIRED | Lines 196, 214: `oldest = job.oldest_seq()` and `new_events = job.snapshot_events(last_seq)`. Synthetic `events.evicted` emitted before real-event yield when gap exists. |
| `templates/job.html` | `routes.py:job_stream(after_seq)` | EventSource URL: `?after_seq={{ job.next_seq }}` | WIRED | Template line 30 emits canonical query string consumed by handler line 164. |
| `jobs.py:archive_story` (render section) | `render_status` dict + `story.done` event with breakdown + `RenderError` after loop | All three renderers run unconditionally; per-renderer try/except marks ok/failed; story.done emits dict; RenderError raised IFF all failed | WIRED | Lines 162-185 implement all four steps in order. `story.done` (lines 176-182) precedes the conditional raise (lines 184-185). |
| `jobs.py:RenderError` | `JobRunner._run except Exception` (runner.py:181-183) | `RenderError(Exception)` flows through unchanged → `set_failed(str(e))` | WIRED | `RenderError` is direct `Exception` subclass; `_run`'s `except Exception as e` catches it; `archive_many` (line 247) also catches via `except Exception as e`. Verified by `test_archive_many_records_render_error_in_results`. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `chapter_html.py:extract_chapter` | `paragraphs[i]["html"]` | `_PARAGRAPH_CLEANER.clean(para.decode_contents())` from BeautifulSoup parse of input HTML | Yes — sanitized real HTML; spot-check produced `hi <img src="a.jpg"><br>x` from a synthetic chapter | FLOWING |
| `comments.py:_parse_one` | `Comment.replies` | Recursive call with `depth+1`, populated from raw payload `replies` field | Yes — 15-level fixture preserves 10 levels populated with real replies; depth-10 leaf carries empty replies | FLOWING |
| `runner.py:Job.events` | `ProgressEvent` instances | `Job.emit()` called by archive pipeline progress callback | Yes — every emit appends; spot-check 1100 emits produce events with seq 101..1100 surviving | FLOWING |
| `routes.py:job_stream` | SSE `data:` payloads | `job.snapshot_events(last_seq)` + synthetic `events.evicted` on gap | Yes — integrated tests in `test_web_routes.py` verify real events with `seq` field reach the client and `events.evicted` payload contains `dropped_count=5`, `requested_after_seq=0`, `oldest_available_seq=6` for the seq-5-cap fixture | FLOWING |
| `jobs.py:archive_story` (story.done payload) | `render_status` dict | Per-renderer try/except assignments inside the render loop | Yes — `test_archive_story_partial_render_failure_does_not_raise` and the all-fail test confirm the dict is populated and emitted with the actual ok/failed breakdown | FLOWING |
| `templates/job.html` | `?after_seq={{ job.next_seq }}` URL | `Job.next_seq` public attribute set by `Job.emit()` | Yes — `test_job_detail_template_renders_after_seq_url` asserts `?after_seq=1` after one emit | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite | `.venv/Scripts/python -m pytest tests/ -q` | `220 passed, 1 skipped, 6 warnings in 22.31s` | PASS |
| Lint clean | `.venv/Scripts/python -m ruff check local_story_archive/` | `All checks passed!` | PASS |
| nh3 import | `python -c "import nh3; assert hasattr(nh3, 'Cleaner')"` | OK | PASS |
| REL-01 cap default | `_MAX_COMMENT_DEPTH == 10`; `_parse_one({"id":"x"})` returns tuple | `REL-01 ok` | PASS |
| REL-02/03 caps | `_MAX_EVENTS_PER_JOB == 1000`, `_MAX_JOBS == 50`; emit increments next_seq; events[0].seq == 1; oldest_seq() == 1 | `REL-02/REL-03 ok` | PASS |
| REL-04 RenderError | `RenderError` is `Exception` subclass distinct from `ResolveError` | `REL-04 ok` | PASS |
| SAN-01 sanitization | `extract_chapter` strips `<script>` and `onerror`, preserves `<img>`/`<br>` | Output: `hi safe<img src="a.jpg"><br>x` | PASS |
| ROADMAP SC1 | 15-level chain via `parse_comments_page` | `WARNING ... comment c15 truncated: replies beyond depth 10 dropped`; 10 levels walkable | PASS |
| ROADMAP SC2 | Chapter with img+br+data-p-id | All three preserved post-sanitization | PASS |
| ROADMAP SC3 | 60 done jobs cap at 50; 1100 emits cap at 1000 | Both assertions hold | PASS |
| No legacy `?after=` | Grep `local_story_archive/` for `?after=` | Zero matches | PASS |
| No legacy `_next_seq`/`after_index` | Grep `local_story_archive/` | Zero matches | PASS |
| No `bleach` in deps | Grep `pyproject.toml` (case-insensitive) | Zero matches | PASS |
| No `job.events|length` in templates | Grep `local_story_archive/web/templates/` | Zero matches | PASS |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|---------------|-------------|--------|----------|
| **REL-01** | 01-02 | Comment-reply recursion is depth-bounded — `_parse_one()` accepts `max_depth` (default 10), truncates deeper replies, logs a warning so silent data loss is visible | SATISFIED | `_MAX_COMMENT_DEPTH = 10` at module scope; `_parse_one` returns `tuple[Comment | None, bool]` with explicit cap guard; `parse_comments_page` emits one `logger.warning` per truncated top-level subtree. 11 unit tests in `test_api_comments.py` (10 new) cover default cap, custom cap, no-truncation case, no-RecursionError on 30 levels, missing id, malformed replies, warning emission, no-warning under cap, one-warning-per-truncated-subtree, and the monkeypatch contract. |
| **REL-02** | 01-03 + 01-05 | `Job.events` keeps the most recent N entries (default 1000) so long archives don't grow unbounded; SSE stream still emits new events | SATISFIED | `Job.events` is `deque(maxlen=_MAX_EVENTS_PER_JOB=1000)`; `ProgressEvent.seq` monotonic from 1 assigned under `Job._lock`; `snapshot_events(after_seq=N)` filters by `e.seq > N` (NOT index); `oldest_seq()` exposes leftmost surviving seq; SSE handler (`routes.py:job_stream`) emits synthetic `events.evicted` payload with `dropped_count`/`requested_after_seq`/`oldest_available_seq` on first-poll gap with per-stream `gap_announced` latch; template renders `?after_seq={{ job.next_seq }}`. Integrated tests in `test_runner.py` (19 new) and `test_web_routes.py` (7 new) verify all behaviors end-to-end. |
| **REL-03** | 01-03 | `JobManager` prunes old jobs — retain only the N most recent (default 50), pruning under existing lock when a new job is created | SATISFIED | `_MAX_JOBS = 50` module constant; `JobManager.create` inserts new job first (so it's never a prune candidate), then iterates `_order` evicting only `JobStatus.done`/`failed` (running and pending pinned per D-13); ROADMAP literal verified: 60 done jobs → exactly 50 retained. Eight unit tests cover at-cap pruning, running-pin (D-13), failed-eviction, overshoot when all running, ROADMAP 60-job literal, just-created safety, `_order` consistency. |
| **REL-04** | 01-04 | If all three renderers fail for a story, the job ends `failed` rather than `done`; partial success surfaces as a per-format flag in the final event | SATISFIED | `RenderError(Exception)` defined adjacent to `ResolveError`; `archive_story` collects `render_status: dict[str, Literal["ok","failed"]]`; emits `story.done` payload with the dict BEFORE conditionally raising `RenderError(f"all renders failed: {render_status}")` (gated on `all(v=="failed" ...)`); `JobRunner._run`'s existing `except Exception` routes to `set_failed`; `archive_many` records the error in its results dict via the same path. 6 new unit tests in `test_jobs.py` cover subclass check, all-fail-raises, partial-fail-no-raise, all-ok-status, renderer-independence-under-failure, archive_many-results-dict integration. |
| **SAN-01** | 01-01 | Paragraph HTML is sanitized at extract-time via `nh3` — `extract_chapter()` runs each paragraph's `html` field through an explicit allowlist before storing; allowlist preserves `<img>`, `<br>`, and `data-p-id` | SATISFIED | Module-scope `_PARAGRAPH_CLEANER = nh3.Cleaner(...)` with D-01..D-03 config: `tags={"img","br","b","i","em","strong","u","a"}`, per-tag attrs `{"img":{"src","alt"},"a":{"href"},"*":{"data-p-id"}}`, `strip_comments=True`. Per-paragraph `.clean(raw_html)` call inside extract loop before assignment to `paragraphs[i]["html"]`. 12 new unit tests in `test_chapter_html.py` verify script/onerror/javascript: stripping, https/http preservation, reading-rich tags, `<br>`+`<img src/alt>` preservation, class/style stripping, narrow data-* allowlist, and HTML-field shape. ROADMAP SC2 spot-check confirmed `<img>`+`<br>`+`data-p-id` all intact post-sanitization. |
| **SAN-02** | 01-01 | `bleach` is replaced by `nh3` in `pyproject.toml` — `nh3 0.3.x` added; `bleach` not introduced | SATISFIED | `pyproject.toml` line 19: `"nh3>=0.3,<0.4",`. Grep for `bleach` (case-insensitive) returns zero matches. `import nh3; nh3.Cleaner` succeeds. |

**All 6 phase requirement IDs SATISFIED. No orphaned requirements. No requirements expected for this phase that lack implementation.**

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| _none_ | — | — | — | — |

Anti-pattern scan run on all phase-modified source files (`pyproject.toml`, `local_story_archive/api/comments.py`, `local_story_archive/jobs.py`, `local_story_archive/scrape/chapter_html.py`, `local_story_archive/web/routes.py`, `local_story_archive/web/runner.py`, `local_story_archive/web/templates/job.html`). Empty-list/dict patterns found are either:
- `replies: list[Comment] = []` initialization in `_parse_one` (legitimate accumulator overwritten by recursion)
- `survivors: list[str] = []` in `JobManager.create` (legitimate accumulator overwritten in the loop)
- `out: list[Comment] = []` in `_fetch_all` (legitimate accumulator)
- `parsed: list[Comment] = []` in `parse_comments_page` (legitimate accumulator)
- `paragraphs: list[dict] = []`, `images: list[str] = []` in `extract_chapter` (legitimate accumulators populated by loop)
- `render_status: dict[...] = {}` in `archive_story` (legitimate accumulator populated by render loop)

None flow to UI as final values — every accumulator is filled by a real data-producing loop before consumption. No `TODO`/`FIXME`/`PLACEHOLDER`/`coming soon`/`not yet implemented` markers introduced. No console.log-only or `=> {}` empty handlers. Lint (`ruff check local_story_archive/`) clean.

### Human Verification Required

None. All behavior is unit-testable and was verified by 220 passing tests (54 new across 5 test files) plus end-to-end probes for each ROADMAP success criterion. SSE behavior was integration-tested through `fastapi.testclient.TestClient` (7 tests in `test_web_routes.py`) including the eviction-gap synthetic event emission, the gap-announced-once latch, the per-event `seq` field, and the rendered template URL — these cover the only realistic "live SSE in browser" gap.

### Gaps Summary

No gaps. Phase 1 fully achieves its goal:
- All 4 ROADMAP success criteria verified end-to-end via direct probe execution
- All 6 requirement IDs (REL-01, REL-02, REL-03, REL-04, SAN-01, SAN-02) SATISFIED with implementation evidence in source + comprehensive unit-test coverage
- All artifacts exist, are substantive (not stubs), are wired (imported and used), and produce real data through the wiring (Level 4 confirmed)
- All key links verified — every truth's supporting code path is connected end-to-end
- Anti-pattern scan clean; lint clean; full test suite passes (220 / 1 skipped)
- No orphaned requirements; no legacy artifacts (`?after=`, `_next_seq`, `after_index`, `bleach`, `job.events|length`) remain
- The Plan 03/05 split (where the breaking `snapshot_events` rename had to ship atomically with its sole consumer in routes.py) was handled correctly per the checker recommendation; the template/test follow-up in Plan 05 closes the loop with no broken-interim-state window

---

_Verified: 2026-05-03_
_Verifier: Claude (gsd-verifier)_
