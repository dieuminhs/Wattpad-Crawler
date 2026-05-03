---
phase: "02"
plan: "03"
subsystem: cli
tags: [auth, cli, gate, AUTH-02]
dependency_graph:
  requires: ["02-01"]
  provides: [cli-auth-gate]
  affects: [wattpad_crawler/cli.py, tests/unit/test_cli.py]
tech_stack:
  added: []
  patterns: [monkeypatch-bypass, inner-try-except-with-outer-finally]
key_files:
  created: []
  modified:
    - wattpad_crawler/cli.py
    - tests/unit/test_cli.py
decisions:
  - "Inner try/except AuthError nested inside outer try/finally so cleanup always runs"
  - "validate_cookie imported at module level into cli.py so monkeypatch works via wattpad_crawler.cli.validate_cookie"
  - "Unused sys and MagicMock imports removed from plan template (ruff F401 auto-fix)"
metrics:
  duration_minutes: 4
  completed_date: "2026-05-03"
  tasks_completed: 2
  files_modified: 2
---

# Phase 02 Plan 03: CLI Auth Gate Summary

**One-liner:** Added `_require_auth(client)` helper gating all four archive branches in `main()` — blank/invalid cookie now exits with `AuthError` on stderr + code 2 before any API call is made.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 0 | Migrate existing archive-branch tests to bypass auth gate | b243e97 | tests/unit/test_cli.py |
| 1 | Add _require_auth + per-branch calls + AuthError catch + 4 new tests | b28b321 | wattpad_crawler/cli.py, tests/unit/test_cli.py |

## What Was Built

### wattpad_crawler/cli.py

- Added `from wattpad_crawler.auth import AuthError, validate_cookie` at module level
- Added `_require_auth(client: RateLimitedClient) -> None` helper (delegates to `validate_cookie`)
- Modified `main()` dispatch block: inner `try/except AuthError` nested inside existing outer `try/finally`
- `_require_auth(client)` called at the top of `story`, `url`, `library`, and `list` branches (4 occurrences)
- `status` and `serve` branches left ungated (AUTH-02 / D-06)
- `AuthError` caught, formatted to stderr as `"AuthError: {e}\nUpdate your cookie via /setup or edit ..."`, returns `2`

### tests/unit/test_cli.py

**Task 0 — Locked migration (4 pre-existing tests):**

Each of these received `monkeypatch.setattr("wattpad_crawler.cli.validate_cookie", lambda client: None)` as their first statement:
1. `test_main_story_calls_archive_story`
2. `test_main_url_command_resolves_then_archives`
3. `test_main_library_calls_fetch_library_and_archive_many`
4. `test_main_list_calls_fetch_list_and_archive_many`

`test_main_status_does_not_make_network_calls` and `test_main_serve_invokes_uvicorn` were NOT modified (exempt per D-06).

**Task 1 — 4 new AUTH-02 tests:**
- `test_main_archive_auth_failure_exits_2` — blank cookie exits 2 with "AuthError" and "/setup" on stderr
- `test_main_all_archive_branches_gated` — parametrized over all 4 archive branches, each gated
- `test_main_status_skips_validation` — status does not call validate_cookie
- `test_main_serve_skips_validation` — serve does not call validate_cookie

**Test count:** 20 test functions (23 total runs — `test_main_all_archive_branches_gated` has 4 parametrized sub-tests). All 23 pass.

## Verification Results

```
pytest tests/unit/test_cli.py -x -q
23 passed in 7.97s

ruff check wattpad_crawler/cli.py tests/unit/test_cli.py
All checks passed!

Quick cross-suite (128 tests):
128 passed, 6 warnings in 39.50s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed unused imports from test template**
- **Found during:** Task 1 ruff check
- **Issue:** Plan template included `import sys` and `from unittest.mock import MagicMock` but neither is used in the actual test implementations
- **Fix:** Removed both unused imports; `AuthError` import (which is used) was retained
- **Files modified:** tests/unit/test_cli.py
- **Commit:** b28b321

## Success Criteria Verification

- [x] AUTH-02 satisfied: archive / library / list / url branches all gated; status / serve skipped
- [x] D-05 satisfied: `_require_auth(client)` called at first line of each archive branch
- [x] D-06 satisfied: status / serve exempt (no `_require_auth` call in those branches)
- [x] D-07 satisfied: no `--skip-auth-check` flag added
- [x] D-08 satisfied: stderr message contains "AuthError:" and "/setup"; `return 2`
- [x] All 4 tests from VALIDATION.md row "02-03-*" exist and pass
- [x] All 4 pre-existing archive-branch tests migrated via locked monkeypatch line
- [x] ROADMAP success criterion #1 (CLI portion) verified by `test_main_archive_auth_failure_exits_2`

## Known Stubs

None.

## Threat Flags

None. Plan's threat register covered: T-02-XX (stderr message content) accepted per single-user local-tool model. No new network endpoints or auth paths introduced.

## Self-Check: PASSED

- [x] `wattpad_crawler/cli.py` exists and contains `_require_auth`
- [x] `tests/unit/test_cli.py` exists and contains all 4 new test functions
- [x] Commits b243e97 and b28b321 exist in git log
- [x] 23 tests pass, ruff clean
