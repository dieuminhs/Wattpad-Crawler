---
phase: 02
plan: 01
subsystem: auth
tags: [auth, cookie-validation, exception-hierarchy, unit-tests]
dependency_graph:
  requires: []
  provides: [wattpad_crawler.auth.AuthError, wattpad_crawler.auth.AuthFailedError, wattpad_crawler.auth.validate_cookie, wattpad_crawler.auth._PROBE_URL]
  affects: [02-02, 02-03, 02-04, 02-05]
tech_stack:
  added: []
  patterns: [TYPE_CHECKING-import-cycle-break, httpx-MockTransport-unit-tests, cookie-jar-reach-in]
key_files:
  created:
    - wattpad_crawler/auth.py
    - tests/unit/test_auth.py
  modified: []
decisions:
  - "_PROBE_URL = https://www.wattpad.com/api/v3/users/wattpad/library?limit=1 (default — Task 1 confirmed unauth contract is HTTP 400 + PermissionDenied, not 401/403/redirect)"
  - "validate_cookie() catches httpx.HTTPStatusError (not AuthFailedError) for non-2xx responses — client.py's raise_for_status() converts all non-2xx to HTTPStatusError before returning"
  - "Cookie blank-check uses client._client.cookies.get('token', domain='wattpad.com') reach-in — this matches build_client()'s jar.set() pattern and worked correctly in tests"
metrics:
  duration_min: 30
  completed_date: "2026-05-03T15:50:02Z"
  tasks_completed: 1
  tasks_total: 1
  files_created: 2
  files_modified: 0
---

# Phase 02 Plan 01: auth.py Module + Unit Tests Summary

Single canonical module for cookie validation — `validate_cookie()` probing `users/wattpad/library?limit=1` with HTTP 400 + PermissionDenied detection, `AuthError`/`AuthFailedError` exception hierarchy, 7 unit tests all passing.

## Task Results

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Manual probe-URL verification | 40721e5 (orchestrator) | (none — verification only) |
| 2 | Create auth.py + test_auth.py | 282406b | wattpad_crawler/auth.py, tests/unit/test_auth.py |

## Task 1 Outcome (from orchestrator resolution)

**_PROBE_URL decision:** Keep default `https://www.wattpad.com/api/v3/users/wattpad/library?limit=1`

**Curl probe outcomes (verified 2026-05-03 against live Wattpad API):**

| Probe | Cookie | Expected (plan) | Actual observed |
|-------|--------|-----------------|-----------------|
| 2 | No cookie | 401 or 3xx-to-/login | HTTP 400 + `{"error_code":1018,"error_type":"PermissionDenied","message":"User not logged in"}` |
| 3 | Bogus cookie (`token=garbage`) | 401 or 3xx-to-/login | HTTP 400 + same body as probe 2 |
| 1 | Valid cookie | 200 | Not run (unauth-contract finding sufficient; valid-cookie path will be exercised by Phase 5 VCR cassette recording) |

**Documented fallback:** `/api/v3/internal/auth/check` returned HTTP 400 + `error_code:1001 InvalidEndpoint` (does not exist — cannot be used as fallback).

**Plan adaptation (commit 40721e5):** `validate_cookie()` extended to detect HTTP 400 + `error_type:"PermissionDenied"` (or `error_code:1018`) as auth failure, in addition to 401/403/redirect. Seventh unit test added asserting this contract.

## Task 2 Implementation Notes

**Cookie jar reach-in:** The plan's primary approach — `client._client.cookies.get("token", domain="wattpad.com")` — worked correctly. The `build_client()` function sets the cookie via `jar.set("token", cfg.cookie, domain="wattpad.com")`, so the domain-scoped lookup finds it. The `make_client()` test helper preserves the jar when replacing `rlc._client`, so the short-circuit test also works correctly. Config-stash fallback was NOT needed.

**Test count:** 7 new tests, all passing.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] validate_cookie response-status checks unreachable after HTTPStatusError**

- **Found during:** Task 2 — test run after initial file creation
- **Issue:** The plan's `validate_cookie` code assumed `client.get()` would return a `Response` object for 401/403/302/400 responses, reaching the `if resp.status_code` branches. In practice, `RateLimitedClient.get()` calls `resp.raise_for_status()` before returning, which raises `httpx.HTTPStatusError` for ALL non-2xx responses (including 3xx and 4xx). The plan's `except AuthFailedError` branch would never trigger, and the redirect/400-PermissionDenied detection blocks after the try/except were dead code.
- **Fix:** Added `except httpx.HTTPStatusError as e:` handler that performs all non-2xx detection from `e.response`: redirect-to-login check (from `e.response.headers["Location"]`), HTTP 400 + PermissionDenied check (from `e.response.json()`), and generic AuthError fallback. The dead `if resp.status_code` blocks after the try/except were collapsed to a single `if 200 <= resp.status_code < 300: return` guard (the only reachable path since raise_for_status already handled non-2xx).
- **Files modified:** `wattpad_crawler/auth.py`
- **Commit:** 282406b

**2. [Rule 1 - Bug] Unused AuthFailedError import in test_auth.py**

- **Found during:** Task 2 — ruff check after initial file creation
- **Issue:** Plan's test code imported `AuthFailedError` but no test directly uses it. Ruff F401 flagged it.
- **Fix:** Removed `AuthFailedError` from the import line in `tests/unit/test_auth.py`.
- **Files modified:** `tests/unit/test_auth.py`
- **Commit:** 282406b (same atomic commit)

## Verification Results

```
pytest tests/unit/test_auth.py -x -q
7 passed in 1.61s

pytest tests/unit/test_client.py tests/unit/test_cli.py tests/unit/test_jobs.py \
       tests/unit/test_web_routes.py tests/unit/test_runner.py -x -q
114 passed, 6 warnings in 28.42s

ruff check wattpad_crawler/auth.py tests/unit/test_auth.py
All checks passed!
```

Note: `test_cli.py`, `test_jobs.py`, `test_web_routes.py`, and `test_runner.py` required `nh3` to be installed (pre-existing missing dep in the venv). Installed via `pip install nh3` — this is an existing pyproject.toml dependency, not a new addition.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The `logger.warning` in `auth.py` logs `Location` header value and HTTP status code only — never the cookie value. Grep confirms zero `logger.*token` or `logger.*cookie` references in `auth.py`. T-02-01 disposition: mitigated.

## Known Stubs

None. `auth.py` is fully implemented with no placeholder logic.

## Self-Check: PASSED

- `wattpad_crawler/auth.py` exists: FOUND
- `tests/unit/test_auth.py` exists: FOUND
- Commit 282406b exists: FOUND (`git log --oneline | grep 282406b`)
- 7 tests passing: VERIFIED (pytest output above)
- ruff clean: VERIFIED
- 114 existing tests passing: VERIFIED
