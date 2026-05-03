---
phase: 02
plan: 05
subsystem: web-setup
tags: [auth, cookie-validation, atomic-write, xss-mitigation, setup-ux]
dependency_graph:
  requires: ["02-01"]
  provides: ["validate-before-save-setup", "atomic-cookie-persistence"]
  affects: ["wattpad_crawler/web/routes.py", "wattpad_crawler/web/templates/setup.html"]
tech_stack:
  added: []
  patterns:
    - "atomic write via same-directory tmp + os.replace (mirrors archive/store.py)"
    - "validate-before-save with transient RateLimitedClient context manager"
    - "three-category error UX (auth / network / unexpected) via Jinja2 auto-escaped template vars"
key_files:
  created: []
  modified:
    - wattpad_crawler/web/routes.py
    - wattpad_crawler/web/templates/setup.html
    - tests/unit/test_web_routes.py
decisions:
  - "response_model=None added to @router.post('/setup') because FastAPI cannot generate a Pydantic schema for RedirectResponse | HTMLResponse union — this is the documented FastAPI pattern for handlers that return multiple response types"
  - "Interim commit between Task 1 and Task 2 made as recommended in execution_note — clean rollback point confirmed"
  - "existing test_setup_post_saves_cookie and test_setup_post_strips_whitespace updated to monkeypatch validate_cookie — required by new validate-before-save behavior (Rule 1 auto-fix)"
metrics:
  duration_minutes: 7
  completed_date: "2026-05-03"
  tasks_completed: 2
  files_changed: 3
---

# Phase 2 Plan 05: /setup Validate-Before-Save + Atomic Cookie Write Summary

**One-liner:** `/setup POST` now validates cookie via transient `RateLimitedClient` before persisting; `_save_cookie` writes atomically via same-directory tmp + `os.replace` with cleanup on exception.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Atomic `_save_cookie` + 3 AUTH-05 tests | c65cbef | `routes.py`, `test_web_routes.py` |
| 2 | Rewrite `setup_post` + error banner template + 4 AUTH-03 tests | 724b4ca | `routes.py`, `setup.html`, `test_web_routes.py` |

## Success Criteria Verification

- [x] AUTH-03 satisfied: `/setup` POST validates BEFORE save; failure re-renders 400 with three-category error banner + masked attempted cookie; success 303-redirects to `/setup?saved=1`
- [x] AUTH-05 satisfied: `_save_cookie` uses tmp + `os.replace` + cleanup-on-exception
- [x] D-09 satisfied: `TemplateResponse` with `status_code=400` on failure
- [x] D-10 satisfied: three error categories (auth / network / unexpected) in template
- [x] D-11 satisfied: `attempted_cookie_masked` rendered back via `_mask`
- [x] D-12 satisfied: `_save_cookie` called only after `validate_cookie` returns `None` (success)
- [x] D-19 satisfied: tmp + `os.replace` + cleanup pattern mirrors `archive/store.py` shape
- [x] D-20 satisfied: `_save_cookie` still lives in `web/routes.py` (not moved)
- [x] ROADMAP success criterion #2 verified by `test_setup_post_invalid_cookie_rerenders`
- [x] ROADMAP success criterion #4 verified by `test_save_cookie_crash_safe`
- [x] T-02-01 mitigated: `error_message` goes through Jinja2 auto-escape (no `|safe`)
- [x] T-02-03 mitigated: `attempted_cookie_masked` goes through Jinja2 auto-escape
- [x] T-02-04 mitigated: same-directory tmp + `os.replace` closes half-written-file race

## Test Results

- **Total in test_web_routes.py:** 38 tests (31 existing + 7 new)
- **7 new tests (all passing):**
  - `test_save_cookie_uses_atomic_pattern` — verifies `os.replace` called with `_config.toml.{pid}.{tid}.tmp` src
  - `test_save_cookie_crash_safe` — verifies `_config.toml` unchanged when `os.replace` raises
  - `test_save_cookie_cleans_up_tmp_on_failure` — verifies no leftover `*.tmp` files after failure
  - `test_setup_post_invalid_cookie_rerenders` — 400 + error banner + `_config.toml` not modified
  - `test_setup_post_valid_cookie_saves` — 303 redirect + cookie persisted
  - `test_setup_post_network_error` — network error renders `error_kind=network` banner
  - `test_setup_post_shows_masked_attempted` — `AbCd…5678` mask visible in response body
- **Full quick suite:** 128 passed (test_auth + test_client + test_cli + test_jobs + test_web_routes + test_runner)
- **Fixture audit:** `grep -c "def client(" tests/unit/test_web_routes.py` → 0 (no shared client fixture)
- **Ruff:** `ruff check wattpad_crawler/web/routes.py tests/unit/test_web_routes.py` → no errors

## Key Implementation Notes

- `dataclasses.replace(cfg, cookie=submitted)` used to clone frozen `Config` with submitted cookie — idiomatic Python for frozen dataclasses
- `RateLimitedClient(transient_cfg)` used as context manager so httpx client is closed even if `validate_cookie` raises — no resource leak
- No `# noqa: BLE001` added — ruff `[E,F,I,UP,W]` selection does not include `BLE` (blind-except rule), so the broad `except Exception` branch is not flagged
- Interim commit made between Task 1 and Task 2 as recommended in `<execution_note>` — clean rollback point at c65cbef

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FastAPI union return type annotation rejected**
- **Found during:** Task 2 (first pytest run after adding `setup_post` rewrite)
- **Issue:** `fastapi.exceptions.FastAPIError: Invalid args for response field! ... starlette.responses.RedirectResponse | starlette.responses.HTMLResponse is a valid Pydantic field type`
- **Fix:** Added `response_model=None` to `@router.post("/setup", response_model=None)` — the documented FastAPI pattern for handlers returning multiple response types
- **Files modified:** `wattpad_crawler/web/routes.py`
- **Commit:** 724b4ca (included in Task 2 commit)

**2. [Rule 1 - Bug] Existing setup tests broken by validate-before-save**
- **Found during:** Task 2 (first pytest run)
- **Issue:** `test_setup_post_saves_cookie` and `test_setup_post_strips_whitespace` POSTed without monkeypatching `validate_cookie` — now returns 400 because no real Wattpad connection available in tests
- **Fix:** Added `monkeypatch.setattr("wattpad_crawler.web.routes.validate_cookie", lambda c: None)` to both tests — same pattern used by all 4 new AUTH-03 tests
- **Files modified:** `tests/unit/test_web_routes.py`
- **Commit:** 724b4ca (included in Task 2 commit)

**3. [Rule 1 - Bug] Ruff I001 import ordering violation**
- **Found during:** Task 2 ruff check
- **Issue:** `from wattpad_crawler.auth import AuthError, validate_cookie` was appended after the existing first-party imports block instead of sorted alphabetically within it
- **Fix:** Moved the auth import between `from wattpad_crawler.archive.state import Manifest` and `from wattpad_crawler.client import RateLimitedClient`
- **Files modified:** `wattpad_crawler/web/routes.py`
- **Commit:** 724b4ca (included in Task 2 commit)

## Known Stubs

None — all template variables are wired to real data. `error_kind`, `error_message`, `attempted_cookie_masked`, and `current_cookie_masked` all flow from live request/config state in `setup_post` and `setup_get`.

## Threat Flags

No new threat surface introduced beyond what the plan's `<threat_model>` already covers. All four mitigations (T-02-01, T-02-03, T-02-04, T-02-XX) are implemented.

## Self-Check: PASSED

- FOUND: wattpad_crawler/web/routes.py
- FOUND: wattpad_crawler/web/templates/setup.html
- FOUND: tests/unit/test_web_routes.py
- FOUND: .planning/phases/02-auth-hardening/02-05-SUMMARY.md
- FOUND commit: c65cbef (Task 1)
- FOUND commit: 724b4ca (Task 2)
- 38 tests passing in test_web_routes.py
- 128 tests passing in full quick suite
- ruff check: no errors
