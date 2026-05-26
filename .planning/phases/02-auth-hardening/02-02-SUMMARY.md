---
phase: 02
plan: 02
subsystem: client
tags: [auth, fast-fail, rate-limited-client, tdd]
dependency_graph:
  requires: [02-01]
  provides: [AUTH-04-partial]
  affects: [local_story_archive/client.py, tests/unit/test_client.py]
tech_stack:
  added: []
  patterns: [deferred-function-scope-import, tdd-red-green]
key_files:
  created: []
  modified:
    - local_story_archive/client.py
    - tests/unit/test_client.py
decisions:
  - Deferred function-scope import of AuthFailedError inside get() to break auth.py <-> client.py circular dependency
  - Detect HTTP 400 + PermissionDenied/1018 as auth failure (Wattpad's actual unauth signal, not 401/403)
  - Plain HTTP 400 responses (e.g. InvalidEndpoint=1001) fall through to raise_for_status — not misclassified as auth
  - Log only URL + status code in warning — never cookie, headers, or response body (T-02-02)
metrics:
  duration_minutes: 15
  completed_date: "2026-05-03T15:58:17Z"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 2
---

# Phase 02 Plan 02: Auth Fast-Fail Branch in RateLimitedClient.get() Summary

Added 401/403/400-PermissionDenied fast-fail detection in `RateLimitedClient.get()` using deferred imports, raising `AuthFailedError` immediately before retry/raise_for_status branches with status_code and url payload per AUTH-04/D-13/D-14/D-15.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Insert 401/403/400-PermissionDenied fast-fail branch + 5 AUTH-04 tests | 11177b7 | local_story_archive/client.py, tests/unit/test_client.py |

## What Was Built

### local_story_archive/client.py

Inserted a new auth fast-fail branch between line 72 (`last_exc = None`) and the existing 429 branch (now at line 120). The branch has two parts:

1. **401/403 detection** (inserted at line 80): Immediately raises `AuthFailedError` with `status_code=resp.status_code` and `url=url`. No retry.
2. **HTTP 400 + PermissionDenied detection** (inserted at line 99): Parses response JSON; raises `AuthFailedError(status_code=400, url=url)` only if `error_type == "PermissionDenied"` or `error_code == 1018`. Plain HTTP 400 (e.g. `InvalidEndpoint=1001`) falls through to existing `raise_for_status()`.

Both branches use a **deferred function-scope import** (`from local_story_archive.auth import AuthFailedError` inside `get()`) to avoid the circular import that would arise from a module-level import (auth.py uses `RateLimitedClient` under `TYPE_CHECKING`).

**Insertion line numbers (final):**
- `if resp.status_code in (401, 403):` → line 80
- `if resp.status_code == 400:` → line 99
- `if resp.status_code == 429:` → line 120 (unchanged, just shifted down)
- `resp.raise_for_status()` → line 130 (unchanged, just shifted down)

### tests/unit/test_client.py

Added `from local_story_archive.auth import AuthFailedError` import (line 8) and 5 new tests appended after the existing 16 tests:

1. `test_get_does_not_retry_on_401` — 401 raises AuthFailedError, handler called exactly once
2. `test_get_raises_on_403` — 403 raises AuthFailedError, handler called exactly once
3. `test_auth_failed_error_payload` — AuthFailedError carries `.status_code == 401` and `.url == "https://www.wattpad.com/x"`
4. `test_get_raises_on_400_permission_denied` — HTTP 400 + `{error_code:1018, error_type:"PermissionDenied"}` raises AuthFailedError with `.status_code == 400`, handler called once (no retry)
5. `test_get_does_not_intercept_400_invalid_endpoint` — HTTP 400 + `{error_code:1001, error_type:"InvalidEndpoint"}` falls through to `httpx.HTTPStatusError`, NOT AuthFailedError

**Total tests in test_client.py: 21 (16 existing + 5 new), all passing.**

## Verification Results

```
pytest tests/unit/test_client.py -x -q
21 passed in 14.56s

pytest tests/unit/test_auth.py tests/unit/test_client.py -x -q
28 passed in 26.85s

ruff check local_story_archive/client.py tests/unit/test_client.py
All checks passed!
```

T-02-02 audit — logger calls in client.py contain no sensitive data:
```
grep -i -E "logger\.(warning|info|debug|error)" local_story_archive/client.py | grep -i -E "(token|cookie|cookies|body|resp\.text|resp\.content)"
(no matches — CLEAN)
```

## Deviations from Plan

None — plan executed exactly as written. The 400 + PermissionDenied detection branch was already specified in the updated plan (per Plan 02-01 Task 1 verification finding).

## Known Stubs

None. All detection logic is fully wired: the branch executes on every `RateLimitedClient.get()` call, which is the choke point for all Wattpad API calls.

## Threat Flags

No new threat surface introduced. The two new logger.warning calls log only `url` (caller-controlled string, already trusted) and `resp.status_code` (integer). T-02-02 confirmed mitigated.

## Self-Check: PASSED

- `local_story_archive/client.py` exists and contains all required substrings
- `tests/unit/test_client.py` exists and defines all 5 required test functions
- Commit `11177b7` exists: `git log --oneline | grep 11177b7` confirms
- All 21 tests pass; ruff clean
