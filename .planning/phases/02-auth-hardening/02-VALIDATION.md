---
phase: 02
slug: auth-hardening
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-03
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Sourced from `02-RESEARCH.md` §"Validation Architecture (Nyquist)".

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.0+ (dev dep `pyproject.toml:24`) |
| **Config file** | `pyproject.toml [tool.pytest.ini_options]` (lines 33-36) |
| **Quick run command** | `pytest tests/unit/test_auth.py tests/unit/test_client.py tests/unit/test_cli.py tests/unit/test_jobs.py tests/unit/test_web_routes.py tests/unit/test_runner.py -x -q` |
| **Full suite command** | `pytest -q` (already excludes `live` marker via `addopts = "-m 'not live'"`) |
| **Estimated runtime** | ~2-5 seconds (quick); ~10-20 seconds (full) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit/test_auth.py tests/unit/test_client.py tests/unit/test_cli.py tests/unit/test_jobs.py tests/unit/test_web_routes.py tests/unit/test_runner.py -x -q`
- **After every plan wave:** Run `pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green; manual probe-URL `curl` verification (documented in `02-RESEARCH.md` §"Probe Endpoint Decision") executed once during Plan 1
- **Max feedback latency:** 5 seconds for the quick command

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-* | 01 (auth.py module) | 1 | AUTH-01 | T-02-01 (cookie reflected in error page) | `validate_cookie` raises `AuthError` on 401 | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_raises_on_401 -x` | ❌ W0 | ⬜ pending |
| 02-01-* | 01 | 1 | AUTH-01 | — | `validate_cookie` raises `AuthError` on 403 | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_raises_on_403 -x` | ❌ W0 | ⬜ pending |
| 02-01-* | 01 | 1 | AUTH-01 | — | `validate_cookie` raises `AuthError` on 3xx → /login redirect | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_raises_on_login_redirect -x` | ❌ W0 | ⬜ pending |
| 02-01-* | 01 | 1 | AUTH-01 | — | `validate_cookie` returns None on 200 | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_passes_on_200 -x` | ❌ W0 | ⬜ pending |
| 02-01-* | 01 | 1 | AUTH-01 | — | `validate_cookie` propagates `httpx.RequestError` (does NOT raise AuthError on transport failure) | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_propagates_network_error -x` | ❌ W0 | ⬜ pending |
| 02-01-* | 01 | 1 | AUTH-01 | — | `validate_cookie` raises `AuthError` immediately on empty cookie (no HTTP call) | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_short_circuits_on_blank -x` | ❌ W0 | ⬜ pending |
| 02-02-* | 02 (client.py 401/403 branch) | 1 | AUTH-04 | T-02-02 (cookie value in log line) | `RateLimitedClient.get` raises `AuthFailedError` on first 401, no retry | unit | `pytest tests/unit/test_client.py::test_get_does_not_retry_on_401 -x` | ❌ W0 | ⬜ pending |
| 02-02-* | 02 | 1 | AUTH-04 | — | Same for 403 | unit | `pytest tests/unit/test_client.py::test_get_raises_on_403 -x` | ❌ W0 | ⬜ pending |
| 02-02-* | 02 | 1 | AUTH-04 | — | `AuthFailedError.status_code` and `.url` populated | unit | `pytest tests/unit/test_client.py::test_auth_failed_error_payload -x` | ❌ W0 | ⬜ pending |
| 02-03-* | 03 (CLI gate) | 2 | AUTH-02 | — | CLI `archive` exits 2 on `AuthError`, prints to stderr, does not call `archive_story` | unit | `pytest tests/unit/test_cli.py::test_main_archive_auth_failure_exits_2 -x` | ❌ W0 | ⬜ pending |
| 02-03-* | 03 | 2 | AUTH-02 | — | CLI `library` / `list` / `url` also gated (parametrized) | unit | `pytest tests/unit/test_cli.py::test_main_all_archive_branches_gated -x` | ❌ W0 | ⬜ pending |
| 02-03-* | 03 | 2 | AUTH-02 | — | CLI `status` does NOT validate (no network) | unit | `pytest tests/unit/test_cli.py::test_main_status_skips_validation -x` | ❌ W0 | ⬜ pending |
| 02-03-* | 03 | 2 | AUTH-02 | — | CLI `serve` does NOT validate at startup | unit | `pytest tests/unit/test_cli.py::test_main_serve_skips_validation -x` | ❌ W0 | ⬜ pending |
| 02-04-* | 04 (jobs.py propagation) | 2 | AUTH-04 | — | `archive_story` propagates `AuthFailedError` (not swallowed by per-part try/except) | unit | `pytest tests/unit/test_jobs.py::test_archive_story_propagates_auth_failed -x` | ❌ W0 | ⬜ pending |
| 02-04-* | 04 | 2 | AUTH-04 | — | `archive_story` emits `auth.failed` event before re-raising | unit | `pytest tests/unit/test_jobs.py::test_archive_story_emits_auth_failed_event -x` | ❌ W0 | ⬜ pending |
| 02-04-* | 04 | 2 | AUTH-04 | — | `JobRunner` ends job with status `failed` and the AuthFailedError message visible | integration (TestClient) | `pytest tests/unit/test_runner.py::test_runner_marks_failed_on_auth_failure -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 (/setup atomic + UX) | 2 | AUTH-03 | T-02-01, T-02-03 (XSS via error_message) | `/setup` POST with auth-failure cookie re-renders 400, banner present, `_config.toml` unchanged | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_invalid_cookie_rerenders -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 | 2 | AUTH-03 | — | `/setup` POST with valid cookie saves and redirects 303 | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_valid_cookie_saves -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 | 2 | AUTH-03 | — | `/setup` POST with network error renders banner with `error_kind="network"` | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_network_error -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 | 2 | AUTH-03 | — | `/setup` POST shows `attempted_cookie_masked` back on error | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_shows_masked_attempted -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 | 2 | AUTH-05 | T-02-04 (race between concurrent writers) | `_save_cookie` writes to tmp + `os.replace()` | unit | `pytest tests/unit/test_web_routes.py::test_save_cookie_uses_atomic_pattern -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 | 2 | AUTH-05 | — | Crash mid-write (monkeypatch `os.replace` to raise) leaves `_config.toml` either unchanged or fully written, never zero bytes | unit | `pytest tests/unit/test_web_routes.py::test_save_cookie_crash_safe -x` | ❌ W0 | ⬜ pending |
| 02-05-* | 05 | 2 | AUTH-05 | — | Tmp file cleanup on exception (no leftover `*.tmp.*` files in archive dir after a failed write) | unit | `pytest tests/unit/test_web_routes.py::test_save_cookie_cleans_up_tmp_on_failure -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

> Task IDs use the wildcard `02-NN-*` pending PLAN.md generation. The planner pins each test to a specific task ID during PLAN.md authoring.

---

## Wave 0 Requirements

- [ ] `tests/unit/test_auth.py` — NEW file. Covers AUTH-01 unit tests (6 tests above).
- [ ] `tests/unit/test_client.py` — extend with 401/403/payload tests (3 new tests).
- [ ] `tests/unit/test_cli.py` — extend with auth-gate tests (4 new tests).
- [ ] `tests/unit/test_jobs.py` — extend with `AuthFailedError` propagation tests (2 new tests).
- [ ] `tests/unit/test_web_routes.py` — extend with /setup error-flow tests (4 new tests) + atomic save tests (3 new tests).
- [ ] `tests/unit/test_runner.py` — extend with one integration-style test that submits a job whose work raises `AuthFailedError` and asserts `JobManager` records it as `failed` (1 new test).
- [ ] No new framework install needed — pytest 8.0+, pytest-vcr, vcrpy, ruff already in dev deps.
- [ ] No new fixtures needed — `tmp_path` covers all file-write tests; `httpx.MockTransport` covers all HTTP tests; `monkeypatch` covers `os.replace` interception.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Probe URL `GET /api/v3/users/wattpad/library?limit=1` returns 401/3xx-to-/login on bad cookie and 200 on valid cookie | AUTH-01 | Requires real Wattpad credentials; STATE.md flagged this as needing one-time live verification | Run the three `curl` commands documented in `02-RESEARCH.md` §"Probe Endpoint Decision" (no cookie, valid cookie, bogus cookie) once during Plan 01 implementation. If `library` probe misbehaves, swap to `/api/v3/internal/auth/check`. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies recorded above
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags (`-x -q` only)
- [ ] Feedback latency < 5s for the quick command
- [ ] `nyquist_compliant: true` set in frontmatter (set by planner once PLAN.md task IDs are pinned)

**Approval:** pending
