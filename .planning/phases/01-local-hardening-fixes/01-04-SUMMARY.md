---
phase: 01-local-hardening-fixes
plan: 04
subsystem: jobs
tags: [render, error-handling, jobs, REL-04]
requires:
  - jobs.archive_story (existing)
  - jobs.archive_many (existing per-story Exception handler)
provides:
  - jobs.RenderError (new Exception subclass)
  - jobs.archive_story render_status dict + story.done payload extension
affects:
  - SSE consumers reading story.done events (now also receive render_status)
  - JobRunner._run failure routing (RenderError flows through existing except Exception)
tech-stack:
  added: []
  patterns:
    - "Custom Exception subclass alongside ResolveError (CONVENTIONS §Error Handling)"
    - "dict[str, Literal['ok', 'failed']] for runtime+type-level enum contract"
    - "Per-format try/except inside loop; aggregate raise after loop (D-15 / RESEARCH §Pitfall 6)"
key-files:
  created: []
  modified:
    - wattpad_crawler/jobs.py
    - tests/unit/test_jobs.py
decisions:
  - "RenderError inherits directly from Exception (not from a JobError base) — matches existing ResolveError pattern; CONVENTIONS §Error Handling"
  - "story.done is emitted BEFORE the RenderError raise so SSE consumers see the per-format breakdown even when the job ends failed (D-15 step 3)"
  - "Raise gated on all-failed (not any-failed) — partial success keeps the job alive so existing artifacts ship"
metrics:
  duration_seconds: 210
  duration_human: "~4 min"
  tasks_total: 2
  tasks_completed: 2
  tests_total: 20
  tests_new: 6
  tests_existing: 14
  completed_at: "2026-05-03T06:11:05Z"
commits:
  - hash: e0e38fa
    type: feat
    scope: 01-04
    message: "add RenderError and render_status dict to archive_story"
  - hash: 89caab4
    type: test
    scope: 01-04
    message: "add REL-04 RenderError unit tests"
---

# Phase 1 Plan 04: REL-04 RenderError + render_status Summary

`archive_story` now raises `RenderError` when all three renderers fail and emits per-format `render_status` in the `story.done` payload, closing REL-04 (job-marked-done with no artifacts).

## What Changed

`wattpad_crawler/jobs.py`:
- Added `from typing import Literal` import.
- Restructured the render section of `archive_story()` to collect per-format `"ok"`/`"failed"` status and emit it in `story.done`.
- Added `RenderError(Exception)` class adjacent to `ResolveError(Exception)`.

`tests/unit/test_jobs.py`:
- Added 6 new unit tests covering REL-04: subclass check, all-fail-raises, partial-fail-no-raise, all-ok-status, renderer-independence-under-failure, archive_many-results-dict-integration.

## Final body of `archive_story`'s render section (D-15 four-step structure)

```python
sd = store.story_dir(cfg.output_dir, story)
emit("render.start", {"story_id": story.story_id})

# REL-04 / D-15: run all three renderers unconditionally, each in its
# own try/except; collect per-format ok/failed status. story.done
# carries the breakdown so SSE consumers see exactly which formats
# succeeded. After the loop, raise RenderError IFF all three failed
# — partial success keeps the job alive so existing artifacts ship.
render_status: dict[str, Literal["ok", "failed"]] = {}
for name, fn in (
    ("txt", render_txt.render_txt),
    ("html", render_html.render_html),
    ("epub", render_epub.render_epub),
):
    try:
        fn(sd)
        render_status[name] = "ok"
    except Exception as e:
        logger.exception("render(%s) failed for %s: %s", name, story.story_id, e)
        emit("render.failed", {"format": name, "error": str(e)})
        render_status[name] = "failed"

emit(
    "story.done",
    {
        "story_id": story.story_id,
        "render_status": render_status,
    },
)

if all(v == "failed" for v in render_status.values()):
    raise RenderError(f"all renders failed: {render_status}")
```

(Note: `ruff format` reflowed the `emit("story.done", {...})` call across multiple lines; the literal text above reflects the on-disk shape after formatting. Semantics match the plan's `<action>` Step A verbatim.)

## RenderError class as written

```python
class RenderError(Exception):
    """All renderers (TXT, HTML, EPUB) failed for one story.

    Raised by archive_story() after the render loop completes when every
    format in render_status is "failed". JobRunner._run catches this as
    a normal Exception and routes to set_failed(str(e)); archive_many
    records it in the per-story results dict via the same path. Partial
    render failures (>=1 format succeeded) do NOT raise — the story.done
    event carries the per-format breakdown instead.
    """

    pass
```

(Plan said `≥1` in the docstring; the en-dash is preserved. The `≥` was rendered as ASCII `>=` to keep the file pure-ASCII, matching CONVENTIONS expectations and the test_jobs.py imports.)

## Test Coverage

`pytest tests/unit/test_jobs.py -v` → **20 passed in 0.55s**

Test count: 6 new + 14 existing (all 14 pre-existing tests still pass — including `test_archive_story_renderers_are_independent`, which is the closest pre-existing analogue to the new render-independence test and was retained unchanged).

New tests:

| Test | Asserts |
|------|---------|
| `test_render_error_is_exception_subclass` | `issubclass(RenderError, Exception)`, `str(RenderError("msg")) == "msg"` |
| `test_archive_story_raises_render_error_when_all_renderers_fail` | `pytest.raises(RenderError)`; error message contains `"txt"`, `"html"`, `"epub"`, `"failed"`; `story.done` emitted BEFORE raise with `render_status={"txt":"failed","html":"failed","epub":"failed"}`; 3 `render.failed` events |
| `test_archive_story_partial_render_failure_does_not_raise` | txt ok + html/epub fail → no raise; `render_status={"txt":"ok","html":"failed","epub":"failed"}`; 2 `render.failed` events (no txt one) |
| `test_archive_story_all_ok_emits_render_status_all_ok` | All ok → no raise; `render_status` all `"ok"`; zero `render.failed` events |
| `test_archive_story_renderers_run_independently_when_one_fails` | txt raising → html and epub mocks each called exactly once |
| `test_archive_many_records_render_error_in_results` | `archive_many(...)` returns `{"42": "failed: all renders failed: ..."}` (existing per-story `except Exception` handler unchanged) |

## ROADMAP §Phase 1 success criterion #4 verification

The plan's `<verification>` block included a literal end-to-end probe (mutates module-level renderer functions via direct assignment, wraps in try/finally to restore). Ran successfully:

```
OK: RenderError raised; story.done has render_status naming all three failed formats
```

(Three `logger.exception()` tracebacks were printed before the OK line — that is the existing per-format failure logging path, preserved by this plan as required.)

## Deviations from Plan

None — plan executed exactly as written.

`ruff format wattpad_crawler/jobs.py` reflowed the `emit("story.done", {...})` call across multiple lines (and similarly reflowed two pre-existing multi-line dict calls within the `for part in story.parts` loop). This is a formatting-only consequence of running `ruff format` per the plan's Action step; semantics match the plan exactly.

## Authentication Gates

None — plan involves no network calls or auth surface.

## Threat Flags

None. This plan reduces threat surface (closes T-04-01 silent-success and T-04-02 partial-success-as-success per the plan's `<threat_model>`); it introduces no new endpoints, file access patterns, or trust-boundary changes.

## Known Stubs

None — this plan is purely behavioral hardening. No UI rendering paths, no placeholder data sources, no `=[]`/`={}` flowing to UI, no `TODO`/`FIXME` introduced.

## archive_many Path — Verified

`archive_many` was NOT modified (per plan must_haves and the threat model). Its existing per-story `except Exception` handler at `wattpad_crawler/jobs.py:218-224` catches `RenderError` unchanged because `RenderError` is an `Exception` subclass. The `test_archive_many_records_render_error_in_results` test verifies this end-to-end: `archive_many(cfg, fake_client, manifest, ["42"], deps=deps)` returns `{"42": "failed: all renders failed: {...}"}` when all three renderers are monkeypatched to raise.

## JobRunner._run Path — Inferred from Plan 03

Per the plan's must_haves point #5: "JobRunner._run path catches RenderError as a normal Exception and routes it to job.set_failed (existing path; verified by integration with Plan 03)". This plan does not re-test that path; the unit-level `pytest.raises(RenderError)` plus `RenderError(Exception)` subclass guarantee is sufficient because Plan 03 already exercised `_run`'s `except Exception` handler. Plan-level assumption per must_haves point #5; not separately verified here.

## Self-Check: PASSED

Verified:
- `wattpad_crawler/jobs.py` — FOUND (modified)
- `tests/unit/test_jobs.py` — FOUND (modified)
- Commit `e0e38fa` (`feat(01-04): add RenderError and render_status dict to archive_story`) — FOUND in `git log`
- Commit `89caab4` (`test(01-04): add REL-04 RenderError unit tests`) — FOUND in `git log`
- `pytest tests/unit/test_jobs.py -v` — 20 passed (verified above)
- `ruff check wattpad_crawler/jobs.py tests/unit/test_jobs.py` — All checks passed (verified above)
- `python -c "from wattpad_crawler.jobs import RenderError, ResolveError; ..."` — prints `ok` (verified above)
- `class RenderError(Exception):` — line 192 of jobs.py
- `render_status: dict[str, Literal["ok", "failed"]] = {}` — line 162 of jobs.py
- `render_status[name] = "ok"` — line 170 of jobs.py
- `if all(v == "failed" for v in render_status.values()):` — line 184 of jobs.py
- `"render_status": render_status` — line 180 of jobs.py
- `emit("render.failed", {"format": name, "error": str(e)})` — line 173 of jobs.py (preserved)
- `from typing import Literal` — line 6 of jobs.py
