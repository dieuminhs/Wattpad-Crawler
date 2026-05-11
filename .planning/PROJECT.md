# Wattpad Crawler

## What This Is

A personal Python tool for archiving Wattpad stories — fetches metadata, chapters, and comments via Wattpad's unofficial API, stores them on disk in an append-only layout, and renders EPUB / HTML / TXT artifacts. Ships both a CLI (`wattpad-crawler`) and a local FastAPI web UI (dashboard, library browser, reader, live progress) for solo use on the owner's machine.

## Core Value

Reliably preserve Wattpad stories the user cares about — without silent failures, dead cookies, or broken scrapers wasting hours of archive time.

## Requirements

### Validated

<!-- Capabilities that exist in the codebase today and have been used. -->

- ✓ CLI commands: `archive` (single story), `list` (reading list), `library` (user library), `status`, `serve` — existing
- ✓ Wattpad API v3 fetchers for stories, chapters, inline + end comments, user library, lists — existing
- ✓ Rate-limited HTTP client with token bucket, backoff, and Retry-After handling — existing
- ✓ SQLite manifest (`_state.sqlite`) tracking story / part status with WAL mode — existing
- ✓ Atomic file writes for story metadata, cover, chapter JSON / HTML / TXT, comment threads — existing
- ✓ Three renderers: TXT, HTML, EPUB (`ebooklib`) — existing
- ✓ FastAPI web UI: `/setup` cookie page, dashboard with three job-launch forms, `/jobs/{id}` detail + SSE progress stream — existing
- ✓ Library browser (`/library`) with cover serving — existing
- ✓ Reader: TOC (`/read/{author}/{dir}`) + per-chapter view + artifact downloads — existing
- ✓ Background `JobRunner` so the web UI runs archive jobs in daemon threads while serving requests — existing
- ✓ Configuration via TOML (`_config.toml`) — cookie, rate limit, user agent — existing
- ✓ Unit-test coverage across api / client / config / jobs / state / store / render / web routes — existing
- ✓ HTML sanitization via `nh3` over paragraph HTML before storage — validated in Phase 1 (SAN-01, SAN-02)
- ✓ Bounded comment-reply recursion (depth cap = 10) preventing stack-overflow on malformed responses — validated in Phase 1 (REL-01)
- ✓ Job history pruning: `JobManager` capped to 50 jobs, `Job.events` deque capped to 1000 with monotonic `seq` cursor and SSE `events.evicted` gap signaling — validated in Phase 1 (REL-02, REL-03)
- ✓ Per-format render error handling: `RenderError` raised only when all three renderers fail; `story.done` carries a `render_status` breakdown so partial-success jobs ship — validated in Phase 1 (REL-04)

### Active

<!-- Current milestone scope: Harden v1. Audit-driven, no new features. -->

- [ ] Parallelize chapter fetching within a story so `workers_per_story` actually controls in-story concurrency, sharing one rate-limit budget
- [ ] Validate Wattpad cookie on save (CLI + `/setup`) with a quick test API call; surface auth failures during a job instead of silently producing empty chapters
- [ ] Circuit-breaker on chapter extraction: if N consecutive chapters return zero `data-p-id` paragraphs (or near-empty text vs. substantial raw HTML), pause loudly with a clear error
- [ ] Circuit-breaker on rate-limit / auth walls: cap consecutive 4xx/5xx and detect IP-throttling patterns, fail fast instead of looping forever
- [ ] Streamed rendering: write HTML / TXT incrementally instead of accumulating in memory; use `ebooklib`'s incremental API or per-chapter intermediate files for EPUB
- [ ] Real integration test: VCR cassette recorded against a small public story, committed to repo, run in CI to detect API breakage early

### Out of Scope

<!-- Explicit boundaries for THIS milestone (Harden v1). Several may be promoted to a future milestone. -->

- New end-user features (search, filtering, multi-account, deltas/refresh, scheduled jobs, reader features) — milestone is hardening only; defer to a "Features v2" milestone
- Public release polish (packaging as a binary, install simplicity, onboarding UX, public ToS warning copy) — single-user audience, no need
- Multi-tenant or multi-user concurrency — explicitly single-user / single-archive-directory; document this rather than engineer for it
- HTTPS certificate pinning, robust XSS sandboxing for shared output — personal-use only; sanitization is the depth we want
- Removing `workers_per_story` from config — we intend to make it real, not delete it
- Migrating off `data-p-id` selector to an alternative data source — out of scope for this pass; circuit-breaker is the chosen mitigation for now
- Persistence of web job history to SQLite — pruning is enough for solo use; defer the persisted-history idea
- fsync-on-critical-path for SQLite WAL — accepted as documented limitation; not a problem for personal use
- Memory-usage monitoring / metrics dashboards — overkill for a personal tool

## Context

**Existing codebase (mapped 2026-05-03 in `.planning/codebase/`):**
- Layered + pipeline architecture: CLI/Web → archive jobs → API/Scrape → Manifest + Store → Renderers
- Sequential per-chapter pipeline (fetch → extract → comments → store) with progress events
- Web concurrency uses background threads via `JobRunner`; CLI is single-threaded
- Single source of truth for "what's been archived" is the SQLite manifest + filesystem layout under `wattpad-archive/stories/<author>/<id>_<slug>/`

**Where we left off (yesterday):**
- Branch `feat/core-cli` shipped: `serve` subcommand, library grid view + cover serving, story TOC + chapter reader, artifact downloads, README web-UI section
- Most recent commit: `docs: map existing codebase` (the codebase audit feeding this PROJECT.md)

**Why hardening now:**
- The audit (`.planning/codebase/CONCERNS.md`) lists ~20 concerns. Many are silent-failure paths that would cost hours of archive time before being noticed (dead cookie, broken selector, render fail, rate-limit ban). The user's worst case is "spent the night archiving and got empty files." Hardening kills that class of failures.
- The user's archives can be long. Sequential chapter fetching + sequential rendering blows out wall-clock time and memory. `workers_per_story` is in config but unused; making it real is the headline win.

**User profile:**
- Single user, this developer, running locally on Windows 11 (PowerShell + Bash via Git Bash available)
- Comfortable in Python 3.11+; prefers explicit, testable code over heavy abstractions
- Tolerant of breakage as long as it's loud, not silent

## Constraints

- **Tech stack**: Python 3.11+ — Already chosen; `pyproject.toml` enforces. Sticks with stdlib + minimal deps where possible.
- **Dependencies (allowed additions)**: `bleach` or `nh3` for sanitization. Test sanitizer choice on EPUB output before committing.
- **Concurrency**: Stay single-process. In-story parallelism via `concurrent.futures.ThreadPoolExecutor`; rate limit shared via the existing `RateLimitedClient` token bucket.
- **Backwards compatibility**: Existing archives on disk must continue to work — no schema breaks to `_state.sqlite` without a migration; no changes to story-directory layout.
- **Wattpad ToS**: Tool already violates ToS by scraping. Don't add anything that increases visibility (e.g., higher default rates, distinguishing user-agent strings).
- **Audience**: Single user. Don't add multi-user, sharing, or onboarding code.
- **Platform**: Windows-first dev environment but should run on macOS/Linux unchanged. No platform-specific paths or APIs.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Hardening milestone before any new features | Audit surfaced ~20 concerns dominated by silent-failure paths — fixing these protects every future feature | — Pending |
| `workers_per_story` becomes per-story chapter-fetch parallelism (not per-job) | Token bucket is shared in `RateLimitedClient`; chapter fetch is I/O-bound. Per-story is the natural unit. | — Pending |
| Circuit-breakers preferred over migrating off `data-p-id` selector | Migration is open-ended R&D; circuit-breaker bounds the blast radius and gives a loud signal when Wattpad changes structure | — Pending |
| Adopted `nh3` over `bleach` for HTML sanitization | Faster, Rust-backed, smaller dep footprint; chose during Phase 1 research. Reading-rich allowlist (img/br/b/i/em/strong/u/a) per CONTEXT.md D-01..D-04. | ✓ Phase 1 |
| Defer EPUB rendering to streaming until measured | EbookLib's incremental API may be sufficient with chunked input; profile first, optimize second | — Pending |
| Web job history capped in memory, not persisted | Single-user; ephemeral history is fine. Persisting requires schema work that doesn't pay back. | — Pending |
| No new end-user features in this milestone | Keeps scope focused; reliability before surface area | — Pending |
| Probe URL kept as `/api/v3/users/wattpad/library?limit=1` despite Wattpad returning HTTP 400 (not 401/403) for unauth | Verified manually 2026-05-03; documented fallback `/api/v3/internal/auth/check` does not exist (404). Plans 02-01 and 02-02 extend detection to include `error_type:"PermissionDenied"` / `error_code:1018` in HTTP 400 bodies. | Phase 2 — Validated |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-03 after Phase 2 (auth-hardening) completion*
