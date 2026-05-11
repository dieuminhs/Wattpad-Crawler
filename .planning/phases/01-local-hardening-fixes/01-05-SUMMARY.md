---
phase: 01-local-hardening-fixes
plan: 05
subsystem: web/templates + web/routes integrated tests (SSE cursor migration)
tags: [sse, template, eviction-warning, integration-tests, jinja2, fastapi-testclient]
requirements_completed: [REL-02]
dependency_graph:
  requires:
    - phase: 01-local-hardening-fixes/03
      provides: "Job.next_seq (public attr), Job.oldest_seq(), snapshot_events(after_seq), routes.py:job_stream(after_seq) handler with events.evicted gap announcement"
  provides:
    - "wattpad_crawler/web/templates/job.html EventSource URL migrated to ?after_seq={{ job.next_seq }} — D-09 closure"
    - "Integrated SSE test suite in tests/unit/test_web_routes.py (7 tests) covering rename, evicted-on-gap, no-evicted-when-no-gap, evicted-only-once, no-gap-when-cursor-advanced, seq-in-payload, template-renders-after_seq"
  affects:
    - "Phase 1 sign-off: zero ?after= references remain anywhere in wattpad_crawler/ (templates, static, routes); end-to-end integration verified"
tech_stack:
  added:
    - "fastapi.testclient.TestClient driven through SSE stream — first integrated SSE coverage in this codebase"
  patterns:
    - "Inline TestClient + Config(output_dir=output_dir) + build_app(cfg) per test (matches the established pre-existing pattern in test_web_routes.py)"
    - "monkeypatch.setattr(runner, '_MAX_EVENTS_PER_JOB', N) BEFORE mgr.create(...) so the deque maxlen factory captures the patched value"
    - "Seed job, emit events, set_done(), then GET /jobs/{id}/stream — set_done() is what makes the SSE generator return so TestClient.get returns the full body"
key-files:
  created:
    - ".planning/phases/01-local-hardening-fixes/01-05-SUMMARY.md"
  modified:
    - "wattpad_crawler/web/templates/job.html (1 line — commit 4a8982f)"
    - "tests/unit/test_web_routes.py (1 import + 7 test funcs, 183 insertions — commit 762a13e)"
key-decisions:
  - "Used the inline TestClient pattern (not a shared fixture) to match the existing 24 pre-existing tests in the file — consistent style trumps minor duplication"
  - "Kept timeout=2.0 on TestClient.get() per the plan literal, accepting Starlette's DeprecationWarning. Removing it would deviate from the plan-specified test code; the warning is non-blocking."
  - "Did not run ruff format on the file — only ruff check is required (plan + project conventions). The file fails ruff format --check only on pre-existing dict/post style outside the scope of this plan."
patterns-established:
  - "SSE integrated testing: emit events on a job, set_done(), TestClient.get(stream URL) — body includes all SSE 'data: <json>' lines + the __status__ terminator"
  - "Eviction-gap test fixture: monkeypatch _MAX_EVENTS_PER_JOB to small value, emit > cap, connect with after_seq=0, expect events.evicted as first SSE message"
metrics:
  tasks_completed: 2
  tasks_total: 2
  duration_minutes: ~12
  completed_date: "2026-05-03T06:22:00Z"
  files_changed: 2
  tests_added: 7
  tests_passing: 31
  tests_in_file_total: 31
---

# Phase 01 Plan 05: SSE template migration + integrated eviction-gap tests Summary

**Closed the REL-02 loop: `templates/job.html` now emits `?after_seq={{ job.next_seq }}` consuming Plan 03's public seq cursor, and 7 new TestClient-driven SSE tests verify the rename, eviction-gap synthetic event shape, the gap-announced-once latch, the seq field on real-event payloads, and the rendered template URL — phase-1 has zero `?after=` references anywhere in `wattpad_crawler/`.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-03T06:09:54Z
- **Completed:** 2026-05-03T06:22:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- `wattpad_crawler/web/templates/job.html` line 30 EventSource URL migrated from the legacy `?after={{ job.events|length }}` (wrong cursor for jobs with > 1000 events because the deque cap silently caps `events|length`) to `?after_seq={{ job.next_seq }}` (monotonic, unaffected by deque eviction)
- 7 new integrated tests in `tests/unit/test_web_routes.py` — each drives the live FastAPI SSE handler through `fastapi.testclient.TestClient`, asserting both the post-Plan-03 contract and the Plan 05 template render
- All 31 tests in `test_web_routes.py` pass (24 pre-existing + 7 new)
- Repo-wide: zero `?after=` substring matches remain anywhere under `wattpad_crawler/` (verified by Grep)
- `ruff check tests/unit/test_web_routes.py` clean

## Task Commits

Each task was committed atomically:

1. **Task 1: Update job.html EventSource URL** — `4a8982f` (feat)
2. **Task 2: 7 integrated SSE tests** — `762a13e` (test)

## What Shipped

### `wattpad_crawler/web/templates/job.html` — line 30

**Before:**

```html
var es = new EventSource("/jobs/{{ job.job_id }}/stream?after={{ job.events|length }}");
```

**After:**

```html
var es = new EventSource("/jobs/{{ job.job_id }}/stream?after_seq={{ job.next_seq }}");
```

Two changes on one line:
1. Query string key: `after` → `after_seq` (matches Plan 03's renamed FastAPI handler param)
2. Jinja2 expression: `job.events|length` → `job.next_seq`

Why `job.next_seq` not `job.events|length`:
- With the deque cap from Plan 03 (`maxlen=1000`), `job.events|length` is at most 1000 — wrong cursor for any job with > 1000 emitted events
- `job.next_seq` is the highest seq ever assigned by `Job.emit()`, monotonic across the job's lifetime, NOT bounded by deque size
- Plan 03 made `next_seq` a public attribute (no leading underscore) precisely to enable this template access

The static `{% for ev in job.events %}` event-log loop earlier in the template is untouched — it still iterates the surviving deque entries on initial page render.

### `tests/unit/test_web_routes.py` — 7 new test functions

| # | Test | Verifies |
|---|------|----------|
| 1 | `test_job_stream_uses_after_seq_query_param_not_after` | D-09 — GET `/jobs/{id}/stream?after_seq=0` returns 200 with all real events + `__status__` terminator in body |
| 2 | `test_job_stream_each_event_payload_includes_seq_field` | REL-02/D-07 — every real-event JSON has `seq` (1, 2 in order) |
| 3 | `test_job_stream_no_evicted_event_when_no_gap` | No false-positive `events.evicted` when nothing was evicted |
| 4 | `test_job_stream_emits_evicted_event_on_gap` | D-10 — `_MAX_EVENTS_PER_JOB=5` + 10 emits + `after_seq=0` → first SSE message is `{kind: "events.evicted", data: {dropped_count: 5, requested_after_seq: 0, oldest_available_seq: 6}, ts: ...}` |
| 5 | `test_job_stream_emits_evicted_only_once_per_stream` | `gap_announced` latch — `events.evicted` substring count == 1 even though the polling loop runs many iterations |
| 6 | `test_job_stream_no_evicted_when_after_seq_advanced_past_gap` | `after_seq=5` against `oldest=6` → no eviction announcement; received seqs == [6,7,8,9,10] |
| 7 | `test_job_detail_template_renders_after_seq_url` | Plan 05's template change visible end-to-end: GET `/jobs/{id}` HTML body contains `?after_seq=1`; no `?after=` and no `job.events|length` leakage |

Test pattern: `_make_test_client(output_dir)` returns `(TestClient(app), app)`. Each test seeds events on `app.state.job_manager.create(...)`, calls `set_done()` so the SSE generator terminates, then issues `client.get("/jobs/{id}/stream?after_seq=N")` and asserts on the raw body text.

## Decisions Made

- Inline TestClient construction per test (not a shared `pytest.fixture`) to match the pattern of the 24 pre-existing tests in `test_web_routes.py`. Consistency with neighbors is more valuable than DRY here.
- Kept `timeout=2.0` on each `TestClient.get()` call per the plan's literal text. Starlette emits a `DeprecationWarning` for this (issue #1108), but removing it would deviate from the plan's specified code and the warning is non-blocking. Future cleanup if Starlette removes the kwarg entirely.
- Did NOT run `ruff format` on `tests/unit/test_web_routes.py` — the project convention only requires `ruff check`, and the file would only be reformatted in the **pre-existing** dict-with-comma sections that are outside this plan's scope. My new code is already conformant.

## Deviations from Plan

None — both tasks executed exactly as written. No bugs to auto-fix, no missing critical functionality discovered, no blocking issues, no architectural questions.

The only minor friction was a worktree-vs-main-repo path confusion early in execution (Edit tool initially targeted the main repo path that the Read tool had loaded). Recovered by reverting the main repo file via `git checkout --` and re-applying to the correct worktree path. No artifacts left in the wrong location.

**Total deviations:** 0
**Impact on plan:** None — plan executed cleanly.

## Issues Encountered

None during planned work.

## Deferred Items

**1. Pre-existing `ruff format` violations in `tests/unit/test_web_routes.py`** — out of scope (Scope Boundary rule)
- Several pre-existing tests use 1-line dict-with-trailing-comma patterns that `ruff format` would split across multiple lines
- My new code (added in this plan) is already `ruff format`-conformant; the file fails only on pre-existing style
- Project convention only mandates `ruff check` (which passes). `ruff format` is opt-in.
- Logged here for visibility; no action taken in this plan

## Pre-existing Behavior Preserved

- All 24 pre-existing tests in `test_web_routes.py` continue to pass
- `templates/base.html`, `templates/dashboard.html`, `templates/library.html`, `templates/setup.html`, `templates/reader.html` — unchanged
- The static `{% for ev in job.events %}` event-log loop in `job.html` (lines 21-23) — preserved exactly; only line 30 changed
- `wattpad_crawler/web/routes.py:job_stream` — unchanged (Plan 03 owns this file)
- `wattpad_crawler/web/runner.py` — unchanged (Plan 03 owns this file)

## Threat Mitigations Applied

| Threat ID | Mitigation Verified |
|-----------|---------------------|
| T-05-01 (template still emits old `?after=` after handler is renamed) | Direct grep of `wattpad_crawler/` shows zero `?after=` matches. Test #7 (`test_job_detail_template_renders_after_seq_url`) asserts `assert "?after=" not in body` on the rendered HTML. |
| T-05-02 (regression: future template edit reintroduces `?after=`) | Test #7 will fail in CI if any future edit puts `?after=` back into the template render. |
| T-05-03 (Jinja2 expression injection via `{{ job.next_seq }}`) | Accepted — `next_seq` is a server-controlled int. No untrusted data flows in. |

## Phase 1 Hand-off

Plan 05 closes the REL-02 loop. With Plans 01-01 → 01-05 all merged:

- REL-01 (comment-recursion cap): shipped Plan 01-02
- REL-02 (event-cap deque + seq cursor + template migration): shipped Plans 01-03 and 01-05
- REL-03 (JobManager prune cap): shipped Plan 01-03
- REL-04 (render-error tolerance): shipped Plan 01-04
- SAN-01, SAN-02 (nh3 sanitization): shipped Plan 01-01

Phase 1 verification suite (per the verification block in PLAN):
- `pytest tests/unit/test_web_routes.py -v` → 31 passed
- `ruff check tests/unit/test_web_routes.py` → clean
- `Get-ChildItem -Path 'wattpad_crawler' -Recurse -File | Select-String -Pattern '\?after='` → no matches

The Plan 03→05 split eliminated the broken-interim-state warning by shipping `runner.py` + `routes.py` atomically in Plan 03 and the template + integrated tests here in Plan 05. The post-Plan-03 / pre-Plan-05 window was graceful (FastAPI silently ignored the unknown `?after=` query param, `after_seq` defaulted to 0, replaying from seq 0 — redundant but functional, no 500).

## Next Phase Readiness

Phase 1 (Local hardening fixes) is complete pending verifier sign-off. Phase 2 (auth-cookie validation) can begin once the orchestrator merges this wave.

## Self-Check: PASSED

- [x] `wattpad_crawler/web/templates/job.html` exists at HEAD — verified via Read; line 30 contains `?after_seq={{ job.next_seq }}`
- [x] `tests/unit/test_web_routes.py` exists at HEAD — verified via Read; 489 lines (was 307); 7 new test functions added
- [x] `.planning/phases/01-local-hardening-fixes/01-05-SUMMARY.md` exists (this file)
- [x] `4a8982f` in git log — verified (`feat(01-05): migrate job.html EventSource URL...`)
- [x] `762a13e` in git log — verified (`test(01-05): add 7 integrated SSE tests...`)
- [x] `pytest tests/unit/test_web_routes.py -v` → 31 passed
- [x] `ruff check tests/unit/test_web_routes.py` → All checks passed
- [x] No `?after=` substring remains in `wattpad_crawler/` (verified via Grep — no matches)
- [x] No `job.events|length` substring remains in `wattpad_crawler/web/templates/job.html` (verified via Grep — no matches)
- [x] All 5 must_haves.truths from the plan verified by tests:
  1. Template renders `?after_seq={{ job.next_seq }}` — test #7 + Read
  2. events.evicted on gap — test #4
  3. No events.evicted without gap — test #3
  4. Real SSE event JSON has seq field — test #2
  5. Zero `?after=` references — Grep
