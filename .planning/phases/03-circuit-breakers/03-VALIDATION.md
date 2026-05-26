---
phase: 3
slug: circuit-breakers
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-05
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `python -m pytest tests/unit/test_circuit_breakers.py tests/unit/test_jobs.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~10 seconds (quick) / ~30 seconds (full, 249+ tests) |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/unit/test_circuit_breakers.py tests/unit/test_jobs.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/unit/ -q`
- **Before `/gsd-verify-work`:** Full suite `python -m pytest tests/ -q` must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

> Filled by planner after PLAN.md tasks are emitted. Each task ID maps to a
> requirement, a test type, and an automated command.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | RES-01 | — | Extraction-empty trips after 3 consecutive (not 2); aborts with "selector likely changed" | unit + integration | `pytest tests/unit/test_circuit_breakers.py tests/unit/test_jobs.py -k "extraction_empty" -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | RES-02 | — | HTTP-wall trips after 5 consecutive 4xx-excl-404/5xx; 404 marks part `gone` and does NOT increment counter | unit + integration | `pytest tests/unit/test_circuit_breakers.py tests/unit/test_jobs.py -k "http_wall or 404_gone" -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | RES-03 | — | Both breakers emit `breaker.opened` SSE event with kind-specific recent payload | integration | `pytest tests/unit/test_jobs.py -k "breaker_opened_event" -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_circuit_breakers.py` — NEW file: `Breaker` isolation tests (covers RES-01, RES-02, RES-03 unit dimension; threshold-not-fire / threshold-fire / record_success-resets / threading-race / kind-specific message text)
- [ ] Append to `tests/unit/test_jobs.py` — integration tests via `archive_story()`:
  - extraction-empty: monkeypatch `_EXTRACTION_EMPTY_CONSECUTIVE=2` and `_HTML_THRESHOLD=10`; inject `parse_chapter` returning empty `ChapterContent`; assert `breaker.opened` event with `breaker == "extraction_empty"`, `body_text_failed` manifest rows for first N-1 parts, `last_error` matches D-10 format, raw HTML written to disk, no `.json`/`.txt`/`_comments-*.json` for extraction-empty parts.
  - http_wall: monkeypatch `_HTTP_WALL_CONSECUTIVE=2`; inject `fetch_chapter_html` raising `httpx.HTTPStatusError(503)`; assert `breaker.opened` with `breaker == "http_wall"` and `recent` containing the status codes.
  - 404 mid-stream: inject one 404 between four 503s; assert part status `gone`, http_wall counter NOT incremented (5×503 still does not trip if interleaved 404 reset would mask it; verify the 5×503 + 1×404 case trips with the 5 503s).
  - boundary heuristic: `text_len = 99 / html_len = 5001` increments; `text_len = 100 / html_len = 5001` does NOT; `text_len = 99 / html_len = 5000` does NOT.
- [ ] `tests/unit/test_runner.py` — sanity test that `CircuitOpenError` raised by `archive_story()` is caught by `JobRunner._run`'s top-level `except Exception` and routed to `set_failed(str(e))`.

*Existing test infrastructure (`_make_deps()`, `output_dir`, `monkeypatch`, `httpx.MockTransport`) is sufficient — no `conftest.py` changes required.*

---

## False-Pass Risks

| Risk | Prevention |
|------|------------|
| Test passes without breaker actually firing (e.g., test only fetches 2 parts when threshold is 3 — never tests the trip) | Always test BOTH N-1 (no-fire) AND N (fires) cases explicitly |
| 404 counter assertion only checks part status, not breaker `_count` | Assert `breaker._count == 0` after 404, OR test 4×4xx + 1×404 + 1×4xx still does NOT trip (5 4xx total, 1 of which was a 404) |
| `breaker.opened` not emitted for http_wall (D-14 pitfall — sibling `except` does not catch raises from inside `except Exception`) | Test asserts `breaker.opened` event present in events list AND `breaker == "http_wall"`. Implementation must use nested-try pattern from RESEARCH.md Pattern 2. |
| Threading race test passes by GIL luck without actually racing | Use `threading.Barrier(2)` to synchronize thread entry into `record_failure` |
| Monkeypatch on wrong name (`from circuit_breakers import _CONST` vs module attribute) | Plan instructs `jobs.py` to import the module (`import local_story_archive.circuit_breakers as cb`) and read `cb._EXTRACTION_EMPTY_CONSECUTIVE` at Breaker-instantiation time |

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `breaker.opened` event renders in `job.html` SSE stream | RES-03 (success criterion #3) | Visual check that the existing `<code>{kind}</code> {JSON.stringify(data)}` template handles the new event payload without UI breakage | 1. Start `local-story-archive serve`. 2. Open job detail page in browser. 3. Trigger archive of a story whose part fixtures cause extraction-empty trip (or run a unit-test-style fixture story). 4. Confirm `breaker.opened` line appears alongside other `part.failed` lines and the JSON payload renders without overflowing or breaking the layout. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (`tests/unit/test_circuit_breakers.py`)
- [ ] No watch-mode flags (pytest non-watch only)
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter (after planner fills task IDs)

**Approval:** pending
