---
phase: 02-auth-hardening
verified: 2026-05-03T17:00:00Z
status: passed
score: 8/8 must-haves verified
overrides_applied: 0
---

# Phase 2: Auth Hardening Verification Report

**Phase Goal:** Dead-cookie and mid-job auth failures produce loud, immediate errors instead of silently archiving empty chapters
**Verified:** 2026-05-03T17:00:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | validate_cookie() raises AuthError on 401/403/redirect-to-login AND HTTP 400+PermissionDenied | VERIFIED | auth.py:64-112 — catches AuthFailedError (from client.py fast-fail), HTTPStatusError (3xx, 400+PermissionDenied), short-circuits on blank cookie. 7 unit tests in test_auth.py all pass. |
| 2 | RateLimitedClient.get() raises AuthFailedError on 401/403/400+PermissionDenied before retry branches | VERIFIED | client.py:80-118 — 401/403 branch at line 80, 400+PermissionDenied branch at line 99, both before 429 branch at line 120 and raise_for_status at line 130. 5 AUTH-04 tests pass. |
| 3 | CLI archive commands call _require_auth before any API call; status/serve exempt | VERIFIED | cli.py:94,98,102,107 — _require_auth called at top of story/url/library/list branches. status branch (line 111) and serve branch (line 114) have no _require_auth call. 4 AUTH-02 tests pass including parametrized over all 4 branches. |
| 4 | AuthError from dead/missing cookie exits CLI with stderr message + return code 2 before any API call | VERIFIED | cli.py:124-131 — except AuthError prints "AuthError: {e}" and remediation hint containing "/setup" to sys.stderr, returns 2. test_main_archive_auth_failure_exits_2 asserts rc==2, "AuthError" in stderr, "/setup" in stderr. |
| 5 | archive_story per-part loop has dedicated except AuthFailedError BEFORE broad except; emits auth.failed with {part_id, status_code, url, message}; re-raises | VERIFIED | jobs.py:145 except AuthFailedError before jobs.py:165 except Exception. emit("auth.failed", ...) at line 158 with all 4 keys. bare raise at line 164. test_archive_story_propagates_auth_failed and test_archive_story_emits_auth_failed_event both pass. |
| 6 | JobRunner marks job failed with auth message when AuthFailedError propagates | VERIFIED | test_runner_marks_failed_on_auth_failure confirms job.status == JobStatus.failed and "simulated auth failure" in job.error. No change to runner.py needed — existing top-level except Exception already covers it. |
| 7 | /setup POST validates cookie via transient RateLimitedClient before writing _config.toml; re-renders 400 with 3-category error banner on failure; 303 on success | VERIFIED | routes.py:92-139 — validate_cookie(transient_client) at line 108, error_kind classification at lines 110/113/116, TemplateResponse(status_code=400) at line 121, _save_cookie only after error_kind check at line 136. 4 AUTH-03 tests pass. |
| 8 | _save_cookie writes atomically via same-directory tmp + os.replace with cleanup on exception | VERIFIED | routes.py:61-68 — PID/TID suffix at line 61, os.replace at line 65, unlink(missing_ok=True) in except at line 67. 3 AUTH-05 tests pass (atomic pattern, crash-safe, tmp cleanup). |

**Score:** 8/8 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `local_story_archive/auth.py` | AuthError, AuthFailedError, validate_cookie, _PROBE_URL | VERIFIED | 113 lines, all symbols present. _PROBE_URL correct. TYPE_CHECKING import cycle-break present. |
| `tests/unit/test_auth.py` | 7 unit tests covering 401/403/redirect/200/network/blank/400-PermissionDenied | VERIFIED | All 7 test functions present and passing. |
| `local_story_archive/client.py` | 401/403/400-PermissionDenied fast-fail branch in get() | VERIFIED | Branch at lines 80-118, before 429 at line 120. Deferred import inside get(). |
| `tests/unit/test_client.py` | 5 new AUTH-04 tests | VERIFIED | test_get_does_not_retry_on_401, test_get_raises_on_403, test_auth_failed_error_payload, test_get_raises_on_400_permission_denied, test_get_does_not_intercept_400_invalid_endpoint all present. |
| `local_story_archive/cli.py` | _require_auth helper + 4 per-branch calls + AuthError catch in main() | VERIFIED | _require_auth at line 73, called 4 times (lines 94/98/102/107), except AuthError at line 124. |
| `tests/unit/test_cli.py` | 4 AUTH-02 tests + 4 pre-existing tests migrated with validate_cookie bypass | VERIFIED | All 4 new tests present. All 4 pre-existing archive-branch tests have monkeypatch.setattr("local_story_archive.cli.validate_cookie", lambda client: None) as first statement. |
| `local_story_archive/jobs.py` | except AuthFailedError branch + auth.failed emit + D-18 comment in archive_many | VERIFIED | except AuthFailedError at line 145, emit at line 158, bare raise at line 164. D-18 comment at line 269 in archive_many. |
| `tests/unit/test_jobs.py` | 2 AUTH-04 tests | VERIFIED | test_archive_story_propagates_auth_failed (line 510), test_archive_story_emits_auth_failed_event (line 535) both present. |
| `tests/unit/test_runner.py` | 1 integration test for JobRunner failing on AuthFailedError | VERIFIED | test_runner_marks_failed_on_auth_failure at line 358, uses enum comparison (no .value), no hasattr guard. |
| `local_story_archive/web/routes.py` | setup_post rewrite with validate-before-save + _save_cookie atomic write | VERIFIED | setup_post at line 92 with response_model=None. _save_cookie at lines 27-68 with atomic write. |
| `local_story_archive/web/templates/setup.html` | Error banner with 3 categories + attempted_cookie_masked fallback in input | VERIFIED | {% if error_kind %} at line 13, auth/network/else at lines 15/17/19, attempted_cookie_masked or current_cookie_masked at line 31. No |safe filter used. |
| `tests/unit/test_web_routes.py` | 7 new tests (3 atomic save + 4 /setup UX) | VERIFIED | All 7 tests present at lines 502-663. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| auth.py:validate_cookie | client.RateLimitedClient | TYPE_CHECKING import string forward ref | WIRED | auth.py:12-13 `if TYPE_CHECKING: from local_story_archive.client import RateLimitedClient` |
| auth.py:validate_cookie | client.get(_PROBE_URL, max_attempts=1, follow_redirects=False) | per-call follow_redirects=False kwarg | WIRED | auth.py:64 `resp = client.get(_PROBE_URL, max_attempts=1, follow_redirects=False)` |
| client.py:RateLimitedClient.get | auth.AuthFailedError | deferred function-scope import inside get() | WIRED | client.py:81 `from local_story_archive.auth import AuthFailedError` (and line 108) |
| cli.py:main | auth.validate_cookie | _require_auth helper invoked at start of each archive branch | WIRED | cli.py:9 module-level import; _require_auth called lines 94/98/102/107 |
| cli.py:main except block | sys.stderr / sys.exit(2) | except AuthError around dispatch block | WIRED | cli.py:124-131 `except AuthError as e: print(..., file=sys.stderr); return 2` |
| jobs.py:archive_story per-part try | AuthFailedError handler before broad except | dedicated except branch | WIRED | jobs.py:145 `except AuthFailedError as e:` before line 165 `except Exception as e:` |
| AuthFailedError handler | emit('auth.failed', ...) | synchronous emit before raise | WIRED | jobs.py:158 emit call before bare raise at line 164 |
| routes.py:setup_post | auth.validate_cookie | transient RateLimitedClient context manager | WIRED | routes.py:107-108 `with RateLimitedClient(transient_cfg) as transient_client: validate_cookie(transient_client)` |
| setup_post error path | TemplateResponse(status_code=400) | error_kind context var triggers banner in setup.html | WIRED | routes.py:121 `status_code=400`; setup.html:13 `{% if error_kind %}` |
| _save_cookie atomic write | os.replace(tmp, config_path) | same-directory tmp + try/except cleanup | WIRED | routes.py:65 `os.replace(tmp, config_path)`, line 67 `tmp.unlink(missing_ok=True)` in except |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| setup.html:error banner | error_kind, error_message | setup_post try/except branches | Yes — from live validate_cookie call result | FLOWING |
| setup.html:cookie input value | attempted_cookie_masked / current_cookie_masked | _mask(submitted) / _mask(cfg.cookie) | Yes — from submitted form data and Config | FLOWING |
| cli.py:stderr AuthError message | AuthError str(e) | validate_cookie raise chain | Yes — from real HTTP response or blank-cookie check | FLOWING |

### Behavioral Spot-Checks

Step 7b: Test suite run as the primary behavioral check (249 tests, 0 failures).

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 249 unit tests pass | `.venv/Scripts/python.exe -m pytest tests/unit/ -q` | 249 passed, 6 warnings in 39.04s | PASS |
| Auth fast-fail branch placement | grep for status_code in (401, 403) before status_code == 429 | lines 80 and 120 confirm correct ordering | PASS |
| No cookie value in log lines (T-02-02) | grep -i for logger calls containing token/cookie/body in client.py | Zero matches | PASS |
| No |safe filter in setup.html (T-02-01/T-02-03) | grep for \|safe in setup.html | Zero matches — Jinja2 auto-escape active | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| AUTH-01 | 02-01 | validate_cookie() probes auth-required endpoint, raises AuthError | SATISFIED | auth.py fully implemented; 7 tests pass |
| AUTH-02 | 02-03 | CLI validates cookie before archive commands; serve exempted | SATISFIED | _require_auth in 4 archive branches; 4 AUTH-02 tests pass |
| AUTH-03 | 02-05 | /setup POST validates before saving; re-renders form on failure | SATISFIED | setup_post rewritten; 4 AUTH-03 tests pass |
| AUTH-04 | 02-02, 02-04 | get() raises AuthFailedError on 401/403; archive_story propagates it | SATISFIED | client.py fast-fail + jobs.py re-raise; 8 tests pass |
| AUTH-05 | 02-05 | _save_cookie() writes atomically via tmp + os.replace() | SATISFIED | atomic write implemented; 3 AUTH-05 tests pass |

No orphaned requirements: REQUIREMENTS.md maps AUTH-01 through AUTH-05 exclusively to Phase 2, and all 5 are claimed by plans in this phase.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| local_story_archive/web/routes.py | 45 | `line.lstrip().startswith("cookie ")` — fragile startswith, requires space | Warning | Hand-edited config without space would append duplicate cookie key. Noted in code review WR-03. Real-world impact: low (tool generates the config). |
| local_story_archive/web/routes.py | 46,51,56 | Raw f-string interpolation of cookie into TOML | Warning | Cookie containing `"` or `\` would produce malformed TOML. Wattpad tokens are alphanumeric so user-impact probability low. Noted in code review WR-01. |
| local_story_archive/auth.py | 50 | `getattr(client._client, "cookies", None)` — private attribute reach-in | Warning | Fragile coupling to RateLimitedClient internals; degrades gracefully (raises AuthError on rename) but loses short-circuit intent. Noted in code review WR-02. |
| local_story_archive/cli.py | 118-119 | serve branch closes manifest/client, then finally closes them again | Warning | Double-close; idempotent in CPython today but relies on undocumented behavior. Noted in code review WR-04. |

All four anti-patterns were identified by the code reviewer in 02-REVIEW.md. None are blockers — they are warnings that could affect robustness in edge cases. No stubs, no TODO/placeholder patterns, no hardcoded empty data in user-facing paths were found.

### Human Verification Required

None. All must-haves are verifiable programmatically. The test suite provides full behavioral coverage for the phase goal. The one live-network behavior (validate_cookie against real Wattpad) was manually verified by the developer on 2026-05-03 as Task 1 of Plan 02-01 — the finding (HTTP 400 + PermissionDenied rather than 401/403/redirect) drove the plan adaptation and is now covered by unit tests using MockTransport.

### Deferred Items

None identified. All Phase 2 success criteria are fully implemented and verified.

### Gaps Summary

No gaps. All 8 observable truths verified, all 12 required artifacts exist and are substantive and wired, all 10 key links confirmed, 249 tests passing, 0 stubs detected.

The four code review warnings (WR-01 through WR-04) are quality improvements, not goal-blocking gaps. They are documented in 02-REVIEW.md for follow-up in a future polish pass.

---

_Verified: 2026-05-03T17:00:00Z_
_Verifier: Claude (gsd-verifier)_
