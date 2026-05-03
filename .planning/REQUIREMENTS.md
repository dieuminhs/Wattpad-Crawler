# Requirements: Wattpad Crawler — Harden v1

**Defined:** 2026-05-03
**Core Value:** Reliably preserve Wattpad stories the user cares about — without silent failures, dead cookies, or broken scrapers wasting hours of archive time.

## v1 Requirements

Hardening pass on the existing Python archiver. No new end-user features. Each requirement either makes a silent failure loud, stops a runaway loop, or bounds an unbounded resource.

### Reliability

- [x] **REL-01**: Comment-reply recursion is depth-bounded — `_parse_one()` accepts a `max_depth` parameter (default 10), truncates deeper replies, and logs a warning so silent data loss is visible
- [x] **REL-02**: Job event lists are capped — `Job.events` keeps the most recent N entries (default 1000) so long archives don't grow unbounded; SSE stream still emits new events
- [x] **REL-03**: `JobManager` prunes old jobs — retain only the N most recent jobs (default 50), pruning under the existing lock when a new job is created
- [x] **REL-04**: Render failures fail the job loudly — if all three renderers (TXT, HTML, EPUB) fail for a story, the job ends `failed` rather than `done`; partial success surfaces as a per-format flag in the final event

### Sanitization

- [x] **SAN-01**: Paragraph HTML is sanitized at extract-time via `nh3` — `extract_chapter()` runs each paragraph's `html` field through an explicit allowlist before storing; allowlist preserves `<img>`, `<br>`, and the `data-p-id` attribute
- [x] **SAN-02**: `bleach` is replaced by `nh3` in `pyproject.toml` — `nh3 0.3.x` added; `bleach` not introduced (it was never present, but documented here so it's never added)

### Authentication

- [ ] **AUTH-01**: A `validate_cookie()` function in a new `auth.py` module probes a session-required Wattpad endpoint and raises `AuthError` on 401/403/redirect-to-login
- [ ] **AUTH-02**: CLI runs cookie validation before starting any archive command (`archive`, `list`, `library`); `serve` is exempted (web `/setup` covers it)
- [ ] **AUTH-03**: `/setup` POST validates cookie before saving; on failure, re-renders the form with an error message and does not overwrite `_config.toml`
- [ ] **AUTH-04**: `RateLimitedClient.get()` recognizes 401/403 mid-job and raises `AuthFailedError`; `archive_story()` propagates it as a job failure with a clear message instead of falling through to empty chapters
- [ ] **AUTH-05**: `_save_cookie()` writes atomically (temp file + `os.replace()`) so concurrent reads never see a half-written `_config.toml`

### Resilience

- [ ] **RES-01**: An extraction-empty circuit-breaker counts consecutive chapters where extracted text is < 100 chars while raw HTML is > 5 KB; opens after 3 consecutive failures and aborts the story with a clear "selector likely changed" error
- [ ] **RES-02**: An HTTP-wall circuit-breaker counts consecutive 4xx (excluding 404) and 5xx responses; opens after 5 consecutive failures and aborts the story with the recent error pattern
- [ ] **RES-03**: Both breakers are scoped to a single story (one breaker pair per `archive_story()` call) and emit `breaker.opened` progress events the web UI displays prominently

### Throughput

- [ ] **THR-01**: Chapter fetching inside one story runs in parallel via `concurrent.futures.ThreadPoolExecutor` sized by `cfg.workers_per_story`; the existing `RateLimitedClient` token bucket serializes the rate across workers
- [ ] **THR-02**: Per-part exceptions are surfaced — every future is awaited via `as_completed` + `future.result()` so worker exceptions don't get swallowed
- [ ] **THR-03**: SQLite connections are made thread-safe — `Manifest.connect()` opens with `check_same_thread=False` and sets `PRAGMA busy_timeout = 5000`
- [ ] **THR-04**: Per-part status writes (`set_part_status`, `update_body_hash`) are safe under concurrent writers — confirmed by tests that race two writers in the same manifest
- [ ] **THR-05**: Progress events ordering is documented — events may interleave under parallelism; SSE clients must not assume ordinal order

### Rendering

- [ ] **REN-01**: TXT renderer writes incrementally — `render_txt()` opens the output file, iterates parts, writes chunks, and closes; never builds the whole story string in memory
- [ ] **REN-02**: HTML renderer writes incrementally — `render_html()` streams header → per-part chunks → footer to disk
- [ ] **REN-03**: EPUB renderer writes atomically — `render_epub()` writes to `<slug>.epub.tmp` then `os.replace()` to final path so an interrupted render never leaves a corrupt `.epub`
- [ ] **REN-04**: EPUB streaming is documented as out of scope — code comment in `render/epub.py` notes ebooklib has no incremental API; flagged for a future milestone if OOM is observed

### Testing

- [ ] **TEST-01**: `pytest-vcr` is removed from dev deps and replaced by `pytest-recording 0.13.x`; `vcrpy` pinned to `>=8.1.1`
- [ ] **TEST-02**: VCR cassette is recorded against a small public canary story (≤ 5 chapters) and committed under `tests/integration/cassettes/`; cassette uses `match_on=["method", "uri"]` so it survives parallel chapter fetches
- [ ] **TEST-03**: `tests/integration/test_end_to_end.py` no longer skips — runs the full `archive_story()` pipeline against the cassette in CI; `--vcr-record=none` means no network access required
- [ ] **TEST-04**: Threading-related code paths have unit tests — circuit-breakers, comment depth cap, sanitization allowlist, manifest under concurrent writes

## v2 Requirements

Deferred to a future "Features v2" or "Polish & Ship" milestone.

### Future Render

- **REN-V2-01**: EPUB streaming via custom ZipFile serializer — only if OOM is actually observed on real archives; significant R&D, ebooklib has no incremental API today
- **REN-V2-02**: Memory monitoring / abort-render-if-low-memory — overkill until measured

### Future Resilience

- **RES-V2-01**: Circuit-breaker auto-resume / half-open state — single-user; manual restart is fine for v1
- **RES-V2-02**: Persisted job history in SQLite — in-memory cap is enough for solo use

### Future Auth

- **AUTH-V2-01**: Cover-fetch status tracked in manifest (`cover_status` column) — needs schema migration; cosmetic until then

## Out of Scope

| Feature | Reason |
|---------|--------|
| Search / filtering in library UI | Hardening milestone, not feature milestone — defer |
| Multi-account / multi-cookie support | Single user; out of scope for personal tool |
| Scheduled / recurring archive jobs | Out of scope; user runs jobs manually |
| Delta / refresh of already-archived stories | Out of scope this milestone; manifest already supports re-runs |
| Reader UX features (bookmarks, annotations) | Hardening only |
| Public release packaging (binary, installer, docs polish) | Single-user audience |
| Pause / Resume button for in-flight jobs | Circuit-breaker + restart is sufficient; ThreadPoolExecutor cancellation is non-trivial |
| Multi-story parallelism on shared rate budget | Submitting multiple web jobs already achieves this; pool-within-pool is a redesign |
| HTTPS certificate pinning | Personal local tool, MITM threat doesn't justify the cost |
| fsync on every SQLite WAL commit | Accepted limitation; consumer hardware doesn't justify the cost |
| Removing `data-p-id` selector dependency entirely | Open-ended R&D; circuit-breaker bounds blast radius instead |
| `pybreaker` / `circuitbreaker` libraries | Wrong abstraction (decorator-per-call vs. consecutive-loop-failure); 25-line implementation fits better |
| `bleach` for sanitization | Deprecated since January 2023; depends on unmaintained html5lib |
| Switching whole pipeline to async httpx | Disproportionate to a hardening milestone — full rewrite required |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REL-01 | Phase 1 | Satisfied |
| REL-02 | Phase 1 | Satisfied |
| REL-03 | Phase 1 | Satisfied |
| REL-04 | Phase 1 | Satisfied |
| SAN-01 | Phase 1 | Satisfied |
| SAN-02 | Phase 1 | Satisfied |
| AUTH-01 | Phase 2 | Pending |
| AUTH-02 | Phase 2 | Pending |
| AUTH-03 | Phase 2 | Pending |
| AUTH-04 | Phase 2 | Pending |
| AUTH-05 | Phase 2 | Pending |
| RES-01 | Phase 3 | Pending |
| RES-02 | Phase 3 | Pending |
| RES-03 | Phase 3 | Pending |
| THR-01 | Phase 4 | Pending |
| THR-02 | Phase 4 | Pending |
| THR-03 | Phase 4 | Pending |
| THR-04 | Phase 4 | Pending |
| THR-05 | Phase 4 | Pending |
| REN-01 | Phase 5 | Pending |
| REN-02 | Phase 5 | Pending |
| REN-03 | Phase 5 | Pending |
| REN-04 | Phase 5 | Pending |
| TEST-01 | Phase 5 | Pending |
| TEST-02 | Phase 5 | Pending |
| TEST-03 | Phase 5 | Pending |
| TEST-04 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 27 total
- Mapped to phases: 27
- Unmapped: 0
- Satisfied: 6/27 (Phase 1 complete; Phases 2–5 pending)

---
*Requirements defined: 2026-05-03*
*Last updated: 2026-05-03 after Phase 1 audit confirmed satisfaction of REL-01..04, SAN-01..02*
