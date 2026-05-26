---
phase: 02
plan: 04
subsystem: jobs
tags: [auth, error-propagation, sse, tdd]
dependency_graph:
  requires: [02-01]
  provides: [AuthFailedError propagation from archive_story, auth.failed SSE event]
  affects: [local_story_archive/jobs.py, tests/unit/test_jobs.py, tests/unit/test_runner.py]
tech_stack:
  added: []
  patterns: [dedicated-except-before-broad, emit-before-raise, tdd-red-green]
key_files:
  created: []
  modified:
    - local_story_archive/jobs.py
    - tests/unit/test_jobs.py
    - tests/unit/test_runner.py
decisions:
  - "Dedicated except AuthFailedError branch placed BEFORE broad except Exception in per-part loop (line 145 vs 165) — Python except is order-sensitive; reversed order would silently swallow AuthFailedError"
  - "auth.failed event emitted synchronously BEFORE bare raise — guarantees SSE consumers see auth signal before __status__: failed sentinel"
  - "archive_many broad-except left unchanged; added D-18 comment noting deferred batch-abort improvement for v2"
  - "No logger.exception call in AuthFailedError branch — JobRunner logs the propagated exception; avoids double-logging"
metrics:
  duration_minutes: 12
  completed_date: "2026-05-03T15:59:38Z"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 3
---

# Phase 2 Plan 04: AuthFailedError Propagation in archive_story Summary

**One-liner:** Dedicated `except AuthFailedError` branch before broad `except Exception` in archive_story per-part loop — emits `auth.failed` SSE event then re-raises so mid-job 401/403 ends the job `failed` with a clear message.

## What Was Built

AUTH-04 / D-16 / D-17 / D-18: `archive_story()`'s per-part try/except previously had a single broad `except Exception` that caught `AuthFailedError` (which IS an Exception), logged it, marked the part `failed`, emitted `part.failed`, and continued to the next chapter. The next chapter would also 401, and so on. The job ended `done` with N chapters marked failed — no clear "your cookie died" signal.

After this plan:
- A new `except AuthFailedError as e:` branch at `local_story_archive/jobs.py:145` intercepts auth failures BEFORE the broad `except Exception` at line 165.
- The branch sets part status `failed` (manifest consistency), emits `auth.failed` with `{part_id, status_code, url, message}`, then bare-`raise`s the exception.
- `AuthFailedError` propagates to `JobRunner._run`'s top-level `except Exception`, which calls `job.set_failed(str(e))` — job ends `failed` with the auth message in `job.error`.
- SSE consumers see `auth.failed` in the event stream before `__status__: failed`.

## Code-Order Audit

Confirmed by `grep -n "except AuthFailedError\|except Exception" local_story_archive/jobs.py`:

```
45:   except Exception as e:   (cover fetch fallback — not in per-part loop)
98:   except Exception as e:   (cover fetch fallback — not in per-part loop)
145:  except AuthFailedError as e:  <-- dedicated branch (per-part loop)
165:  except Exception as e:        <-- broad branch (per-part loop)
192:  except Exception as e:   (render loop — separate)
268:  except Exception as e:   (archive_many per-story — unchanged)
```

`except AuthFailedError` at line 145 correctly precedes `except Exception` at line 165 within the per-part loop.

## auth.failed Payload Confirmation

`emit("auth.failed", {...})` at `local_story_archive/jobs.py:155` includes all four required keys:
- `part_id` — string part identifier
- `status_code` — int (401 or 403) from `e.status_code`
- `url` — string from `e.url` (Wattpad URL we constructed, not attacker-controlled)
- `message` — `str(e)` from the AuthFailedError message

The `emit` call appears before the bare `raise` statement — D-17 satisfied.

## Tests Added

3 new tests, all passing:

| Test | File | Validates |
|------|------|-----------|
| `test_archive_story_propagates_auth_failed` | test_jobs.py | D-16: AuthFailedError re-raised, part status != done |
| `test_archive_story_emits_auth_failed_event` | test_jobs.py | D-17: auth.failed event emitted before raise, all 4 payload keys present |
| `test_runner_marks_failed_on_auth_failure` | test_runner.py | AUTH-04 integration: JobRunner marks job failed with auth message |

`test_runner_marks_failed_on_auth_failure` uses verified patterns (enum comparison `JobStatus.failed`, no `.value`, no `hasattr` guard, no `runner.shutdown()`). Variable named `job_runner` (not `runner`) to avoid shadowing the imported `runner` module.

## Test Results

- `pytest tests/unit/test_jobs.py tests/unit/test_runner.py -x -q`: **54 passed**
- `pytest tests/unit/test_auth.py test_client.py test_cli.py test_jobs.py test_web_routes.py test_runner.py -x -q`: **124 passed, 6 warnings**
- `ruff check local_story_archive/jobs.py tests/unit/test_jobs.py tests/unit/test_runner.py`: **All checks passed**

## Deviations from Plan

None — plan executed exactly as written. One minor ruff fix applied: the assertion message on `auth_events` length was refactored from a 101-char f-string to two lines to comply with the 100-char line limit configured in `pyproject.toml`. Semantics unchanged.

## Known Stubs

None. The auth.failed event payload is fully wired. No placeholder data flows to rendering.

## Threat Flags

None. The auth.failed payload contains only: `part_id` (string from Wattpad metadata), `status_code` (int 401/403), `url` (string we constructed from wattpad.com host), `message` (str(AuthFailedError)). The cookie value is not included. SSE rendering uses Jinja2 auto-escape (existing, Phase 1).

## Self-Check: PASSED

- `local_story_archive/jobs.py` exists and contains `from local_story_archive.auth import AuthFailedError`: confirmed
- `local_story_archive/jobs.py` contains `except AuthFailedError as e:`: confirmed (line 145)
- `local_story_archive/jobs.py` contains `emit("auth.failed",`: confirmed (line 155)
- `local_story_archive/jobs.py:archive_many` contains comment with "AuthFailedError": confirmed (line 263)
- `tests/unit/test_jobs.py` contains `test_archive_story_propagates_auth_failed`: confirmed
- `tests/unit/test_jobs.py` contains `test_archive_story_emits_auth_failed_event`: confirmed
- `tests/unit/test_runner.py` contains `test_runner_marks_failed_on_auth_failure`: confirmed
- Commit `8423be9` exists: confirmed
