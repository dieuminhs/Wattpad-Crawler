# Roadmap: Wattpad Crawler — Harden v1

## Overview

This milestone hardens an existing, working archiver against a well-documented class of silent failures: dead cookies that fill archives with empty chapters, broken selectors that archive hundreds of blank files before anyone notices, unbounded loops on 429s, and memory spikes from large stories. There are no new user-facing features. Every phase is either a localized single-file fix, a new standalone module, or an additive change to the core pipeline — all building on the existing layered architecture without restructuring it.

The five phases follow a strict dependency chain driven by the audit. Isolated low-risk fixes land first so the riskier pipeline changes in later phases stand on a stable foundation. The integration test lands last so the cassette is recorded against the final parallel pipeline.

## Phases

- [ ] **Phase 1: Local hardening fixes** - Bounded comment recursion, nh3 sanitization, job/event memory caps — three isolated single-file changes safe to merge first
- [ ] **Phase 2: Auth hardening** - Cookie validation on CLI startup and /setup POST, mid-job auth detection, atomic config write — new standalone auth.py module
- [ ] **Phase 3: Circuit-breakers** - Extraction-empty and HTTP-wall circuit-breakers in the archive pipeline; must land after Phase 1 so sanitization is in place
- [ ] **Phase 4: In-story parallelism** - Wire workers_per_story to ThreadPoolExecutor; requires Phase 3 circuit-breakers so parallel silent failures abort loudly
- [ ] **Phase 5: Streaming renders and integration test** - Incremental TXT/HTML rendering, atomic EPUB write, VCR cassette recorded against final parallel pipeline

## Phase Details

### Phase 1: Local hardening fixes
**Goal**: Silent data-loss paths and unbounded resource growth are eliminated without touching the archive pipeline
**Depends on**: Nothing (first phase — isolated single-file modifications)
**Requirements**: REL-01, REL-02, REL-03, REL-04, SAN-01, SAN-02
**Success Criteria** (what must be TRUE):
  1. A synthetic comment fixture with 15 levels of nested replies is parsed without RecursionError; the log contains a depth-cap warning and the top-level Comment object is preserved with replies truncated at depth 10
  2. An HTML chapter containing `<img src="...">`, `<br>`, and a `data-p-id` attribute is extracted; the stored paragraph `html` field contains all three intact after sanitization via nh3
  3. Running the web UI and submitting 60+ jobs does not grow `JobManager._jobs` beyond the 50-job cap; a long-running story's Job object does not grow past 1000 events
  4. A story where all three renderers (TXT, HTML, EPUB) raise exceptions results in a job with `status == "failed"` and a progress event naming which formats failed rather than a job with `status == "done"`
**Plans**: TBD
**UI hint**: no

### Phase 2: Auth hardening
**Goal**: Dead-cookie and mid-job auth failures produce loud, immediate errors instead of silently archiving empty chapters
**Depends on**: Nothing (Phase 2 is independent — new module, no pipeline changes)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05
**Success Criteria** (what must be TRUE):
  1. Running `wattpad-crawler archive <story_id>` with a blank or obviously-invalid cookie prints an `AuthError` message and exits before making any archive API calls
  2. A `/setup` POST with an invalid cookie re-renders the setup form with an error message; `_config.toml` is not modified
  3. A mid-job 401/403 response from `RateLimitedClient.get()` causes the job to end `failed` with a clear "authentication failed" message rather than marking chapters as empty-done
  4. A crash simulated between the start and end of a `_save_cookie()` write leaves `_config.toml` either fully written or fully unchanged — never zero bytes or partial
**Plans**: TBD
**UI hint**: no

### Phase 3: Circuit-breakers
**Goal**: A broken Wattpad selector or sustained HTTP-wall failure aborts a story loudly after a small number of consecutive failures instead of archiving hundreds of empty files
**Depends on**: Phase 1 (extraction-empty breaker is only meaningful after nh3 sanitization is in place; empty `content.text` must mean truly empty, not stripped-by-sanitizer)
**Requirements**: RES-01, RES-02, RES-03
**Success Criteria** (what must be TRUE):
  1. Simulating a page where `data-p-id` paragraphs are absent (e.g., by stripping them from the fixture) causes archive to abort with a "selector likely changed" error after exactly 3 consecutive extraction-empty chapters; the breaker does not fire on 2
  2. Simulating 5 consecutive non-200/non-404 HTTP responses (e.g., 429, 503) causes the story to abort with an HTTP-wall error message; a 404 mid-stream does not increment the HTTP-wall counter and marks the part as `"gone"` instead
  3. Both circuit-breaker events appear as `breaker.opened` progress events in the SSE stream visible on the job detail page
**Plans**: TBD
**UI hint**: no

### Phase 4: In-story parallelism
**Goal**: Setting `workers_per_story = N` in `_config.toml` produces N concurrent chapter fetches that share the existing rate-limit budget; worker exceptions surface as per-part failures rather than being swallowed
**Depends on**: Phase 3 (circuit-breakers must be in place so a broken-selector parallel run aborts loudly with `CircuitOpenError` instead of silently writing N empty parts in parallel)
**Requirements**: THR-01, THR-02, THR-03, THR-04, THR-05
**Success Criteria** (what must be TRUE):
  1. Setting `workers_per_story = 3` produces 3 simultaneous chapter requests visible in the debug log (multiple `GET` lines at the same timestamp); total archive time for a 9-chapter story is less than 3x a single-chapter fetch time
  2. A worker thread that raises an exception results in a `part.failed` progress event and a manifest row with `status == "failed"`; the job does not silently report `story.done` with empty part files
  3. Two worker threads calling `set_part_status()` concurrently on the same manifest produce no `OperationalError: database is locked` and no `ProgrammingError: SQLite objects created in a thread` — confirmed by a unit test that races N=3 writers
  4. The `__status__: done` SSE sentinel is not emitted until all futures in the executor have resolved (no early stream termination while workers are still writing)
**Plans**: TBD
**UI hint**: no

### Phase 5: Streaming renders and integration test
**Goal**: TXT and HTML renderers write output incrementally without accumulating full-story content in memory; the EPUB write is atomic; a VCR-cassette integration test runs offline in CI against the final parallel pipeline
**Depends on**: Phase 4 (VCR cassette must be recorded against the parallel pipeline so `match_on=["method","uri"]` cassette ordering is correct from first recording)
**Requirements**: REN-01, REN-02, REN-03, REN-04, TEST-01, TEST-02, TEST-03, TEST-04
**Success Criteria** (what must be TRUE):
  1. `render_txt()` and `render_html()` each open a temp file, write part content incrementally (chapter by chapter via file handles), and rename to the final path — confirmed by reading the implementation; no full-story string is held in a variable
  2. A simulated crash (exception raised) immediately after `epub.write_epub()` completes but before `os.replace()` leaves no `.epub` file at the final path and no partial file blocks future re-render
  3. Running `pytest -m integration --vcr-record=none` passes without any network access; the cassette replays correctly with `workers_per_story = 3` (parallel fetch order does not break replay)
  4. Running `pytest -m "not live"` (default CI invocation) includes circuit-breaker, depth-cap, sanitization allowlist, and concurrent-manifest-write unit tests with no skips
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in order: 1 → 2 → 3 → 4 → 5
(Phase 2 is independent of Phase 1 but coarse granularity serializes them; can be swapped if convenient.)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Local hardening fixes | 0/? | Not started | - |
| 2. Auth hardening | 0/? | Not started | - |
| 3. Circuit-breakers | 0/? | Not started | - |
| 4. In-story parallelism | 0/? | Not started | - |
| 5. Streaming renders and integration test | 0/? | Not started | - |
