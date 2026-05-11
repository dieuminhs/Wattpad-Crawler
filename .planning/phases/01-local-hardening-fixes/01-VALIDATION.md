---
phase: 01
slug: local-hardening-fixes
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-03
mode: reconstructed_from_artifacts
---

# Phase 01 — Validation Strategy

> Per-phase validation contract reconstructed retroactively from PLAN/SUMMARY artifacts after Phase 1 completion. All 6 phase requirements have automated verification.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.0+ |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]` — testpaths=["tests"], `addopts = "-m 'not live'"`) |
| **Quick run command** | `.venv/Scripts/python -m pytest tests/unit/test_<module>.py -q` |
| **Full suite command** | `.venv/Scripts/python -m pytest tests/ -q` |
| **Estimated runtime** | ~22 seconds (220 passed, 1 skipped) |
| **Lint command** | `.venv/Scripts/python -m ruff check wattpad_crawler/ tests/` |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit/test_<module>.py -q` for the touched module
- **After every plan wave:** Run `pytest tests/ -q` (full suite)
- **Before `/gsd-verify-work`:** Full suite must be green + `ruff check` clean
- **Max feedback latency:** ~22 seconds (full suite); ~1 second (per-module)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | SAN-02 | T-01-01 | nh3>=0.3,<0.4 pinned; bleach absent; nh3.Cleaner importable | smoke | `python -c "import nh3; assert hasattr(nh3, 'Cleaner')"` | ✅ | ✅ green |
| 01-01-02 | 01 | 1 | SAN-01 | T-01-01 / T-01-03 | Module-scope `_PARAGRAPH_CLEANER` + per-paragraph `.clean()` inside `extract_chapter`; reading-rich allowlist (D-01..D-04); explicit `*: {data-p-id}` (NOT `generic_attribute_prefixes`) | unit | `pytest tests/unit/test_chapter_html.py -q` | ✅ | ✅ green |
| 01-01-03 | 01 | 1 | SAN-01 | T-01-01 / T-01-02 / T-01-03 | 12 tests covering script-strip, onerror-strip, javascript:-href-strip, https:-preserved, http:-preserved, data-p-id-preserved, reading-rich tags, br+img-src-alt, class/style-stripped, disallowed-tag-keeps-text, narrow data-* allowlist, html-field shape | unit | `pytest tests/unit/test_chapter_html.py -q` | ✅ | ✅ green |
| 01-02-01 | 02 | 1 | REL-01 | — | `_MAX_COMMENT_DEPTH=10`; `_parse_one(raw, depth, *, max_depth) -> tuple[Comment\|None, bool]`; truncates at cap with `replies=[]` preserving parent; warning fires once per truncated top-level subtree in `parse_comments_page` | unit | `pytest tests/unit/test_api_comments.py -q` | ✅ | ✅ green |
| 01-02-02 | 02 | 1 | REL-01 | — | 10 tests: default-cap, custom-cap, no-truncation-under-cap, no-RecursionError-on-30-level, missing-id, malformed replies, warning-emitted-on-truncation, no-warning-under-cap, one-warning-per-truncated-subtree, monkeypatch-contract | unit | `pytest tests/unit/test_api_comments.py -q` | ✅ | ✅ green |
| 01-03-01 | 03 | 1 | REL-02, REL-03 | T-03-01..T-03-05 | `Job.events: deque(maxlen=1000)`; monotonic `next_seq` (PUBLIC); `ProgressEvent.seq`; `snapshot_events(after_seq)`; `oldest_seq()`; `JobManager._MAX_JOBS=50`; insert-then-prune; running pinned (D-13) | unit | `pytest tests/unit/test_runner.py -q` | ✅ | ✅ green |
| 01-03-02 | 03 | 1 | REL-02, REL-03 | T-03-01..T-03-05 | 19 tests covering REL-02 (event-cap, seq monotonicity, deque maxlen, snapshot filtering, oldest_seq) and REL-03 (under-cap, pruning, running-pin, failed-eviction, overshoot, ROADMAP 60-job literal, just-created safety, _order consistency) | unit | `pytest tests/unit/test_runner.py -q` | ✅ | ✅ green |
| 01-03-03 | 03 | 1 | REL-02 | T-03-06 | `routes.py:job_stream(after_seq)` accepts renamed param; emits synthetic `events.evicted` with `dropped_count`/`requested_after_seq`/`oldest_available_seq` on first-poll gap; per-stream `gap_announced` latch; each real event JSON has `seq` field | integration | `pytest tests/unit/test_web_routes.py -q` | ✅ | ✅ green |
| 01-04-01 | 04 | 1 | REL-04 | T-04-01 / T-04-02 | `RenderError(Exception)` adjacent to `ResolveError`; `render_status: dict[str, Literal["ok","failed"]]`; `story.done` emitted BEFORE conditional raise; raise gated on all-failed (not any-failed) | unit | `pytest tests/unit/test_jobs.py -q` | ✅ | ✅ green |
| 01-04-02 | 04 | 1 | REL-04 | T-04-01 / T-04-02 | 6 tests: subclass-check, all-fail-raises, partial-fail-no-raise, all-ok-emits-status, renderer-independence-under-failure, archive_many-records-RenderError-in-results-dict | unit | `pytest tests/unit/test_jobs.py -q` | ✅ | ✅ green |
| 01-05-01 | 05 | 2 | REL-02 | T-05-01 / T-05-02 | `templates/job.html` line 30 EventSource URL: `?after_seq={{ job.next_seq }}` — zero `?after=` references remain anywhere under `wattpad_crawler/` | template render | `pytest tests/unit/test_web_routes.py::test_job_detail_template_renders_after_seq_url -q` | ✅ | ✅ green |
| 01-05-02 | 05 | 2 | REL-02 | T-05-01..T-05-03 | 7 integrated SSE tests via `fastapi.testclient.TestClient`: rename-applied, seq-in-payload, no-evicted-when-no-gap, evicted-on-gap, evicted-only-once-per-stream, no-evicted-when-cursor-advanced-past-gap, template-renders-after_seq | integration | `pytest tests/unit/test_web_routes.py -q` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Requirements Coverage Roll-Up

| Requirement | Tests | Test File(s) | Coverage |
|-------------|-------|--------------|----------|
| **REL-01** (depth-bounded comment recursion) | 10 | `test_api_comments.py` | COVERED |
| **REL-02** (bounded `Job.events` + seq cursor + SSE eviction signaling + template) | 11 (runner) + 7 (web routes) = 18 | `test_runner.py`, `test_web_routes.py` | COVERED |
| **REL-03** (`JobManager` prune cap with running-pin) | 8 | `test_runner.py` | COVERED |
| **REL-04** (`RenderError` on all-renderers-fail; per-format `render_status`) | 6 | `test_jobs.py` | COVERED |
| **SAN-01** (paragraph HTML sanitized at extract-time via nh3 allowlist) | 12 | `test_chapter_html.py` | COVERED |
| **SAN-02** (`nh3` 0.3.x added; `bleach` not introduced) | smoke | `pyproject.toml` declarative + `import nh3` implicit in every chapter_html test | COVERED (infra-level) |

**Total new tests:** 54 across 5 test files (12 + 10 + 19 + 6 + 7).
**Full suite:** 220 passed, 1 skipped, 6 warnings.

---

## Wave 0 Requirements

Existing infrastructure covered all phase requirements. No Wave 0 work needed:

- pytest 8.x + ruff 0.5.x already installed (`pyproject.toml` `[project.optional-dependencies] dev`)
- `tests/conftest.py` already provides shared fixtures
- All 5 test files (`test_chapter_html.py`, `test_api_comments.py`, `test_runner.py`, `test_jobs.py`, `test_web_routes.py`) pre-existed; new tests appended per plan
- No new framework, plugin, or fixture infrastructure required

---

## Manual-Only Verifications

All phase behaviors have automated verification.

The verifier (`/gsd-verify-phase`) additionally ran 4 ROADMAP success-criterion probes (15-level chain, img+br+data-p-id, 60-job/1100-event caps, all-renderers-fail) end-to-end as one-shot scripts. Those probes are not stored as pytest fixtures — they live in the verification report — but each ROADMAP truth has matching pytest coverage in the per-task map above.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (every task in the per-task map has a green pytest command)
- [x] Wave 0 covers all MISSING references (none — existing infra sufficient)
- [x] No watch-mode flags
- [x] Feedback latency < 30s (full suite ~22s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-05-03 (reconstructed retroactively after phase verification passed; all 6 requirements + 4 ROADMAP success criteria green)

---

## Validation Audit 2026-05-03

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Mode | State B — reconstructed from PLAN/SUMMARY/VERIFICATION artifacts |

Reconstructed from: `01-01-PLAN.md`..`01-05-PLAN.md`, `01-01-SUMMARY.md`..`01-05-SUMMARY.md`, `01-VERIFICATION.md`. Coverage cross-checked against test collection (`pytest --collect-only`, 112 tests in the 5 phase files); full suite re-run green (220 passed, 1 skipped).
