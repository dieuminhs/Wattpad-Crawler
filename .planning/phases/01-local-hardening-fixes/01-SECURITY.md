---
phase: 01-local-hardening-fixes
plans_audited: [01-01, 01-02, 01-03, 01-04, 01-05]
status: secured
asvs_level: 1
block_on: critical
threats_total: 21
threats_closed: 21
threats_open: 0
threats_mitigate: 16
threats_accept: 5
threats_transfer: 0
unregistered_flags: 0
audit_date: 2026-05-03
auditor: gsd-secure-phase
---

# Phase 01 — Security Audit Report

**Phase scope:** Local hardening fixes (SAN-01/SAN-02 sanitization, REL-01 comment recursion, REL-02/REL-03 bounded web resources + SSE cursor, REL-04 render error handling).

**Result:** All 21 declared threats closed. 16 mitigations verified against source; 5 accepted risks logged. No unregistered threat flags surfaced from executor SUMMARY files. ASVS L1 baseline appropriate for single-user local tool.

---

## Verification Summary

| Plan | Mitigate | Accept | Transfer | Total | Closed |
|------|----------|--------|----------|-------|--------|
| 01-01 (SAN-01/SAN-02) | 3 | 1 | 0 | 4 | 4 |
| 01-02 (REL-01) | 3 | 0 | 0 | 3 | 3 |
| 01-03 (REL-02/REL-03) | 5 | 2 | 0 | 7 | 7 |
| 01-04 (REL-04) | 3 | 1 | 0 | 4 | 4 |
| 01-05 (REL-02 closure) | 2 | 1 | 0 | 3 | 3 |
| **Total** | **16** | **5** | **0** | **21** | **21** |

---

## Threat Verification — Mitigated (16)

### Plan 01-01 — HTML Sanitization (SAN-01, SAN-02)

| ID | Category | Disposition | Evidence |
|----|----------|-------------|----------|
| T-01-01 | Tampering / Stored XSS | mitigate | `local_story_archive/scrape/chapter_html.py:15-23` — `_PARAGRAPH_CLEANER = nh3.Cleaner(tags={img,br,b,i,em,strong,u,a}, attributes={img:{src,alt}, a:{href}, *:{data-p-id}}, strip_comments=True)`. Per-paragraph `.clean(raw_html)` call at line 59 inside the `for para in para_els:` loop. Default `url_schemes` excludes `javascript:`/`data:` (no override). Test: `tests/unit/test_chapter_html.py:76` `test_extract_chapter_strips_script_in_paragraph`, `:90` `test_extract_chapter_strips_onerror_handler`, `:120` `test_extract_chapter_strips_javascript_href_keeps_link_text`. |
| T-01-02 | Information disclosure (link rel) | mitigate | `local_story_archive/scrape/chapter_html.py:15-23` — `_PARAGRAPH_CLEANER` does NOT pass `link_rel=None`; nh3 default `link_rel="noopener noreferrer"` preserved by omission. Confirmed in 01-01-SUMMARY decisions section. |
| T-01-03 | Tampering (over-broad data-* allowlist) | mitigate | `local_story_archive/scrape/chapter_html.py:20` — explicit `"*": {"data-p-id"}`; file does NOT contain `generic_attribute_prefixes` (verified via Grep across `local_story_archive/`). Test: `tests/unit/test_chapter_html.py:217` `test_extract_chapter_strips_data_attributes_other_than_p_id` (asserts `data-tracking` stripped, `data-p-id` retained). |

### Plan 01-02 — Comment Recursion (REL-01)

| ID | Category | Disposition | Evidence |
|----|----------|-------------|----------|
| T-02-01 | DoS (RecursionError) | mitigate | `local_story_archive/api/comments.py:16` — `_MAX_COMMENT_DEPTH = 10`. Guard at line 42: `if depth >= max_depth:` returns parent Comment with `replies=[]` (line 43). `_parse_one` signature line 19-24 matches plan. Tests: `tests/unit/test_api_comments.py:39` `test_parse_one_caps_recursion_at_default_max_depth`, `:89` `test_parse_one_no_recursion_error_on_30_level_chain`. |
| T-02-02 | Information disclosure (silent loss) | mitigate | `local_story_archive/api/comments.py:84-92` — `if was_truncated:` block calls `logger.warning("comment %s truncated: replies beyond depth %d dropped", comment.comment_id, _MAX_COMMENT_DEPTH)` once per truncated top-level subtree. Module-level `logger = logging.getLogger(__name__)` at line 8. Test: `tests/unit/test_api_comments.py:124` `test_parse_comments_page_logs_warning_on_truncation`. |
| T-02-03 | Tampering (malformed dict) | mitigate | `local_story_archive/api/comments.py:34-36` — `cid = raw.get("id"); if cid is None: return None, False`. Reply iteration also guards `isinstance(r, dict)` at line 53 and at top level line 78. Test: `tests/unit/test_api_comments.py:99` `test_parse_one_returns_none_when_id_missing`, `:107` `test_parse_one_skips_non_dict_replies`. |

### Plan 01-03 — Bounded Web Resources (REL-02, REL-03)

| ID | Category | Disposition | Evidence |
|----|----------|-------------|----------|
| T-03-01 | DoS (Job.events memory) | mitigate | `local_story_archive/web/runner.py:15` — `_MAX_EVENTS_PER_JOB = 1000`. `Job.events` typed `deque[ProgressEvent]` with `default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB)` at line 45. `Job.emit` at line 56-60 wraps `self.events.append(...)` in `with self._lock`. Test: `tests/unit/test_runner.py:172` `test_job_events_deque_evicts_oldest_at_maxlen` (1100 emits → 1000 retained). |
| T-03-02 | DoS (JobManager._jobs memory) | mitigate | `local_story_archive/web/runner.py:16` — `_MAX_JOBS = 50`. `JobManager.create` lines 115-141 inserts FIRST (line 123) then prunes under `_lock` with predicate `j.status in (JobStatus.done, JobStatus.failed)` at line 133-136. Test: `tests/unit/test_runner.py:316` `test_jobmanager_60_jobs_caps_at_50_when_done`. |
| T-03-03 | Race (deque mutate vs iterate) | mitigate | `local_story_archive/web/runner.py:56-60` (emit) and `:81-89` (snapshot_events) both wrap their work in `with self._lock:`. Pre-existing `test_job_emit_is_thread_safe` continues to pass per 01-03-SUMMARY. |
| T-03-04 | Race (just-created job evicted) | mitigate | `local_story_archive/web/runner.py:123-124` — `self._jobs[job.job_id] = job; self._order.append(job.job_id)` precedes the `if len(self._jobs) > _MAX_JOBS:` block at line 125. New job has `status=pending`, skipped by prune predicate. Test: `tests/unit/test_runner.py:326` `test_jobmanager_just_created_job_is_never_pruned`. |
| T-03-06 | Information disclosure (silent SSE eviction) | mitigate | `local_story_archive/web/routes.py:178-212` — `event_gen` initializes `gap_announced = False` (line 186); on first iteration checks `if not gap_announced:` (line 195), reads `oldest = job.oldest_seq()`, and if `oldest and last_seq + 1 < oldest:` yields synthetic `events.evicted` with payload `{dropped_count, requested_after_seq, oldest_available_seq}` (lines 199-211). Test: `tests/unit/test_web_routes.py:388` `test_job_stream_emits_evicted_event_on_gap` asserts `dropped_count == 5`, `requested_after_seq == 0`, `oldest_available_seq == 6`. Latch verified by `:423` `test_job_stream_emits_evicted_only_once_per_stream`. |

### Plan 01-04 — Render Error Handling (REL-04)

| ID | Category | Disposition | Evidence |
|----|----------|-------------|----------|
| T-04-01 | Data integrity (job done with no artifacts) | mitigate | `local_story_archive/jobs.py:184-185` — `if all(v == "failed" for v in render_status.values()): raise RenderError(f"all renders failed: {render_status}")`. `RenderError(Exception)` defined at line 192. Flows through `JobRunner._run` `except Exception` handler unchanged (`runner.py:181-183`). Test: `tests/unit/test_jobs.py:272` `test_archive_story_raises_render_error_when_all_renderers_fail`. |
| T-04-02 | Information disclosure (silent partial-success) | mitigate | `local_story_archive/jobs.py:162` — `render_status: dict[str, Literal["ok", "failed"]] = {}`. Lines 170 / 174 record `"ok"` / `"failed"` per format. `emit("story.done", {... "render_status": render_status})` at lines 176-182. Test: `tests/unit/test_jobs.py:335` `test_archive_story_partial_render_failure_does_not_raise` asserts payload `render_status == {"txt":"ok","html":"failed","epub":"failed"}`. |
| T-04-04 | Integrity (raise inside loop loses partial info) | mitigate | `local_story_archive/jobs.py:163-174` — render loop body contains only the per-format try/except + status assignment; the `if all(...)` raise is at line 184, AFTER the `emit("story.done", ...)` call at line 176 and AFTER the loop closes. story.done is emitted BEFORE raise, preserving SSE visibility of breakdown even when job ends failed. |

### Plan 01-05 — SSE Template Closure (REL-02)

| ID | Category | Disposition | Evidence |
|----|----------|-------------|----------|
| T-05-01 | Information disclosure (template emits old `?after=`) | mitigate | `local_story_archive/web/templates/job.html:30` — `var es = new EventSource("/jobs/{{ job.job_id }}/stream?after_seq={{ job.next_seq }}");`. Repo-wide grep across `local_story_archive/` for `?after=`, `after_index`, `_next_seq` returns zero matches (verified by auditor). |
| T-05-02 | Information disclosure (regression reintroduces `?after=`) | mitigate | `tests/unit/test_web_routes.py:469` `test_job_detail_template_renders_after_seq_url` asserts `"?after_seq=" in body`, `"?after_seq=1" in body`, `"?after=" not in body`, `"job.events|length" not in body` — fails CI on regression. |

---

## Threat Verification — Accepted (5)

The following accepted risks have been reviewed and confirmed appropriate for single-user local tool scope (per `<context>` and CLAUDE.md project constraints).

| ID | Category | Acceptance Rationale | Source |
|----|----------|----------------------|--------|
| T-01-04 | DoS (oversized HTML triggers nh3 OOM) | Wattpad paragraphs are typically <4KB. nh3 is Rust-backed; no observed OOM on realistic inputs. Out of scope for Phase 1. | 01-01-PLAN.md `<threat_model>` |
| T-03-05 | Integrity (cap overshoots when all running) | Documented behavior: when 60 jobs are submitted and 55 are still running, `_jobs` will hold 60 entries until some finish. Better than orphaning an active JobRunner thread. Test `test_jobmanager_overshoots_when_all_running` (`tests/unit/test_runner.py:305`) locks the contract. | 01-03-PLAN.md `<threat_model>` |
| T-03-07 | Tampering (malformed `?after_seq=`) | FastAPI default behavior: 422 with JSON validation error for non-int `after_seq` — sufficient for personal-use UI. No additional handling needed. | 01-03-PLAN.md `<threat_model>` |
| T-04-03 | Confidentiality (path leak in error string) | Error messages already include exception strings via existing `logger.exception` pattern; project policy permits path-leaking errors in local logs (single-user tool, logs are local, per CLAUDE.md). | 01-04-PLAN.md `<threat_model>` |
| T-05-03 | Tampering (Jinja2 expression injection) | `{{ job.next_seq }}` is a server-controlled int (`Job.next_seq: int = 0` in `runner.py:52`, only mutated by `Job.emit` under `_lock`). No untrusted data flows into the template expression. | 01-05-PLAN.md `<threat_model>` |

All 5 accepted risks have explicit acceptance rationale in their respective PLAN threat tables and no contradictory implementation was found.

---

## Unregistered Threat Flags

**None.**

Inspected SUMMARY files for `## Threat Flags` sections:

- **01-01-SUMMARY.md** (line 140): "No new security-relevant surface introduced beyond what was already scoped in the plan's `<threat_model>`."
- **01-02-SUMMARY.md**: No `## Threat Flags` section; threats explicitly mapped in the must-haves verification table.
- **01-03-SUMMARY.md**: No `## Threat Flags` section; "Threat Mitigations Applied" table maps every T-03-* ID to verifying test.
- **01-04-SUMMARY.md** (line 159): "None. This plan reduces threat surface; it introduces no new endpoints, file access patterns, or trust-boundary changes."
- **01-05-SUMMARY.md**: No `## Threat Flags` section; "Threat Mitigations Applied" table maps every T-05-* ID to verifying test.

No threat flags surfaced from executors that require new threat-register entries.

---

## Cross-Cutting Verifications

The following invariants were checked across the phase scope:

| Check | Result |
|-------|--------|
| `nh3>=0.3,<0.4` declared in `pyproject.toml` | PASS (`pyproject.toml:19`) |
| `bleach` not declared anywhere in `pyproject.toml` | PASS (Grep: no matches) |
| No `?after=` substring in `local_story_archive/` (templates, static, routes) | PASS (Grep: no matches) |
| No `after_index` substring in `local_story_archive/` | PASS (Grep: no matches) |
| No `_next_seq` substring in `local_story_archive/` (public name only) | PASS (Grep: no matches) |
| No `generic_attribute_prefixes` in `local_story_archive/` (anti-pattern) | PASS (Grep: no matches) |
| No `url_schemes=` override in `local_story_archive/` (default preserved) | PASS (Grep: no matches) |
| `RenderError(Exception)` adjacent to `ResolveError(Exception)` in `jobs.py` | PASS (`jobs.py:188`, `:192`) |

---

## ASVS L1 Posture

This phase targets ASVS Level 1 (opportunistic) for a single-user local tool. Relevant L1 controls verified within scope:

- **V5.3 Output Encoding & Injection Prevention** — nh3 sanitization at storage boundary (T-01-01..03)
- **V11 Business Logic** — depth-bounded recursion (T-02-01) prevents adversarial-payload DoS
- **V12 File and Resource** — bounded in-memory deque + JobManager pruning (T-03-01..04) prevents memory exhaustion
- **V8.3 Sensitive Private Data** — render errors do leak path-like info via `str(e)`; T-04-03 explicitly accepts this for local-logs-only tool per project policy

L1 controls outside Phase 1 scope (cookie validation, transport security, etc.) are addressed in subsequent phases per the roadmap.

---

## Audit Trail

| Step | Action | Evidence |
|------|--------|----------|
| 1 | Loaded all 5 PLAN.md `<threat_model>` blocks (21 threats, dispositions tabulated) | 01-01-PLAN through 01-05-PLAN |
| 2 | Loaded all 5 SUMMARY.md `## Threat Flags` (or equivalent mitigations table) | 01-01-SUMMARY through 01-05-SUMMARY |
| 3 | Read implementation: `chapter_html.py`, `comments.py`, `runner.py`, `routes.py`, `jobs.py`, `templates/job.html`, `pyproject.toml` | files cited in evidence column |
| 4 | Verified each `mitigate` claim by reading cited file/line and confirming pattern presence | 16 verifications passed |
| 5 | Verified each `accept` claim has explicit rationale in PLAN and no contradictory implementation | 5 acceptances confirmed |
| 6 | Verified each test cited in SUMMARY tables exists at named function (Grep) | All cited tests present |
| 7 | Cross-cutting greps for forbidden substrings (`?after=`, `after_index`, `_next_seq`, `bleach`, `generic_attribute_prefixes`, `url_schemes=`) | All return zero matches |
| 8 | SUMMARY threat-flag sections reviewed for unregistered surfaces | None present |

---

## Verdict

**SECURED.** All 21 threats in Phase 1's threat register are closed. 16 mitigations verified against source code at the cited file/line; 5 accepted risks reviewed and confirmed appropriate for single-user local tool scope. No unregistered threat flags emerged from executor SUMMARY files. Phase 1 may proceed to merge.

*This audit is read-only. No implementation files were modified. Findings are advisory; closing security findings against ASVS L1 does not preclude future hardening (cookie validation, transport security, etc.) addressed in later phases.*
