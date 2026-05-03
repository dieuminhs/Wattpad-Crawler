# Project Research Summary

**Project:** Wattpad Crawler — Harden v1
**Domain:** Python web-scraper hardening — parallelism, sanitization, circuit-breaking, integration testing
**Researched:** 2026-05-03
**Confidence:** HIGH

## Executive Summary

This is a personal archiving tool built around a well-understood synchronous Python pipeline (httpx + BeautifulSoup + SQLite + ebooklib). The hardening milestone has no new end-user features — it eliminates a specific class of silent failures that make overnight archive runs unreliable: dead cookies that produce empty chapters, broken selectors that archive garbage, unbounded loops on 429s, and memory spikes from large stories. Every decision in this research is evaluated against that failure class, not against general engineering best practices.

The recommended approach is incremental and additive: no rewrites, no new architectural layers. The existing synchronous pipeline is kept intact. Parallelism is added via ThreadPoolExecutor inside the existing archive_story() loop, reusing the already-thread-safe RateLimitedClient token bucket. Sanitization is added at extract-time with nh3 (replacing the deprecated bleach). Two lightweight in-process circuit-breakers handle the two distinct failure modes (extraction-empty vs. HTTP-wall). Testing is hardened with pytest-recording backed by vcrpy 8.x, replacing the broken pytest-vcr integration.

The key risk is the parallelism phase: four separate correctness concerns must be resolved before any parallel chapter writes are safe — check_same_thread on the SQLite connection, PRAGMA busy_timeout, future exception surfacing, and VCR cassette order-independence. The recommended mitigation is to complete a Phase A of fully independent, single-file fixes first, so that the riskier pipeline changes in Phase D land on a stable foundation. Build order is the critical constraint for this milestone, not any single feature.

## Key Findings

### Recommended Stack

The existing stack requires only two additions and one swap in dev tooling. All other hardening work uses stdlib. nh3 0.3.5 replaces the deprecated bleach for HTML sanitization — it is Rust-backed, approximately 20x faster, actively maintained, and has a superset of bleach's allowlist API. For integration testing, pytest-vcr must be replaced by pytest-recording 0.13.4 (incompatible plugins — only one can be active). vcrpy must be pinned to >=8.1.1 because 8.0.0 rewrote httpx support via httpcore patching and is the first stable release that works reliably with the project's httpx stack. All circuit-breaker, parallelism, and pruning work uses concurrent.futures, threading, and sqlite3 from the Python 3.11 stdlib.

**Core technologies (additions/swaps only):**
- nh3 0.3.5: HTML sanitization — replaces deprecated bleach; Rust-backed ammonia bindings, ~20x faster, EPUB-safe output
- pytest-recording 0.13.4: VCR integration test plugin — replaces incompatible pytest-vcr; wraps same vcrpy, adds --record-mode CLI flag
- vcrpy >=8.1.1: VCR cassette engine — pin to 8.x for stable httpx/httpcore support; 7.x had breaking httpx issues
- concurrent.futures.ThreadPoolExecutor (stdlib): in-story chapter parallelism — no new dep; existing TokenBucket is already thread-safe
- threading.Lock (stdlib): circuit-breaker state protection — same pattern already used in TokenBucket
- Custom ConsecutiveFailureBreaker (~25 lines): circuit-breaking — do NOT use pybreaker, circuitbreaker, or purgatory; all three are decorator-based per-function-call libraries that do not fit the N-consecutive-loop-failures pattern

**What NOT to add:**
- bleach: officially deprecated January 2023; depends on unmaintained html5lib
- pybreaker / circuitbreaker: designed for distributed microservices; wrong abstraction for this loop
- async httpx / asyncio.gather: requires full pipeline rewrite; disproportionate to a hardening milestone
- zipstream-new for EPUB streaming: EPUB ZIP ordering requirements make custom streaming fragile and equivalent to reimplementing ebooklib

### Expected Features

All hardening work targets a single-user audience with no new UI surface. Every item is defensive — it either makes a silent failure loud, stops a runaway loop, or bounds a resource that currently grows without limit.

**Must have (table stakes — Harden v1 is incomplete without these):**
- Cookie validation on startup (CLI + /setup) — kills the dead-cookie failure class
- Auth-failure detection mid-run — catches cookies that expire during a multi-hour archive; raises AuthError loudly instead of falling through to empty chapters
- Extraction integrity check + circuit-breaker — if data-p-id selector breaks, stops after N consecutive empty-extraction chapters instead of archiving 300 blank files
- HTTP error circuit-breaker — caps consecutive 4xx/5xx; kills the looping-on-429-forever failure class
- Bounded comment recursion — prevents stack-overflow from malformed/adversarial API responses
- HTML sanitization via nh3 — sanitize at extract-time (one call site in scrape/chapter_html.py), not render-time; stored archive is the source of truth and must be clean
- Failure summary at job end (CLI + web) — N parts failed without log-grep
- Render failure surfaced loudly — all renderers failed = job status failed, not done
- Job event list cap + JobManager history pruning — kills memory growth for long web sessions
- In-story chapter parallelism (workers_per_story wired) — config key exists but is ignored; this is the headline throughput win
- Streamed HTML/TXT rendering — O(1) memory per story for text formats; EPUB streaming deferred (ebooklib has no incremental API)
- VCR integration test (canary story cassette committed, skip removed) — makes API breakage detectable without running the real tool

**Defer to future milestone:**
- EPUB streaming rendering — profile first; no OOM observed yet; ebooklib has no streaming API; custom ZipFile path is significant R&D
- Circuit-breaker auto-resume / half-open state — single-user tool; manual restart is sufficient
- Persistent job history to SQLite — in-memory cap is sufficient for solo use; schema work has no payback here
- Cover fetch cover_status tracking in Manifest — cosmetic; needs a migration script

**Explicit anti-features for this milestone:**
- No new web UI pages or reader features
- No Pause/Resume button — circuit-breaker pauses the job state; user restarts; ThreadPoolExecutor graceful interruption is out of scope
- No multi-story parallelism — launching multiple web jobs already achieves that; pool-within-pool on shared rate budget is a significant design change

### Architecture Approach

All hardening work integrates into the existing layered pipeline without restructuring it. The integration points are: jobs.py (parallelism + circuit-breakers), scrape/chapter_html.py (sanitization), api/comments.py (recursion cap), web/runner.py (job/event pruning), and a new auth.py module (cookie validation). The renderers get only a streaming refactor for TXT and HTML; EPUB gets an atomic temp-file write fix and a documented limitation comment. The test infrastructure gets the pytest-recording migration and a committed cassette.

**Major components and hardening touches:**

1. jobs.py:archive_story() — Replace sequential part loop with ThreadPoolExecutor; add two _Breaker counter objects; wire cfg.workers_per_story
2. auth.py (new) — validate_cookie() raises AuthError; called from cli.py before archive_story() and from web/routes.py before runner.submit()
3. scrape/chapter_html.py:extract_chapter() — Add nh3.clean() per paragraph; add extraction-integrity ratio check
4. api/comments.py:_parse_one() — Add depth / max_depth parameters; truncate and log warning at limit
5. archive/state.py:Manifest — Add check_same_thread=False and PRAGMA busy_timeout = 5000 to connect()
6. web/runner.py:JobManager — Add _prune() called inside create() under lock; cap Job.events at 1000 entries
7. render/txt.py + render/html.py — Replace accumulate-then-write with incremental shutil.copyfileobj streaming; render/epub.py gets atomic temp-path write
8. tests/integration/test_end_to_end.py — Remove pytest.mark.skip; add VCR config with match_on=[method, uri]; commit cassette

**Shared-state safety under parallelism:**

| Resource | Safety mechanism |
|----------|-------------------|
| TokenBucket._tokens | threading.Lock — already in place |
| Manifest.set_part_status | SQLite WAL + busy_timeout = 5000 — must be added |
| Job.events | threading.Lock on Job.emit() — already in place |
| _Breaker._count | threading.Lock — must be added to _Breaker |
| Part files on disk | Unique per-part path — no collision by design |

### Critical Pitfalls

The 16 pitfalls researched collapse into 5 priority groups:

1. **check_same_thread on shared Manifest connection** — Python sqlite3 raises ProgrammingError immediately if a Manifest created on the main thread is used in a worker thread. Must be fixed before any parallel chapter write is attempted. Fix: add check_same_thread=False + PRAGMA busy_timeout = 5000 to Manifest.connect().

2. **Futures silently swallow part exceptions** — Exceptions inside ThreadPoolExecutor workers are stored on the Future; a bare try/except around the submit loop does not catch them. Without per-future try/except around fut.result(), failed parts appear as in_progress and jobs silently appear to succeed with empty output.

3. **nh3 allowlist must explicitly include img, br, and data-p-id** — A minimal default allowlist strips img tags (images disappear from EPUB silently), br tags (paragraphs merge), and data-p-id attributes (comment anchoring breaks). The allowlist must be designed before the sanitization call is written. Required: SAFE_TAGS includes p, br, b, i, em, strong, span, a, img; SAFE_ATTRS includes a[href], img[src,alt], *[data-p-id].

4. **VCR cassette order-independence must be configured before recording** — VCR default sequential-index matching breaks when chapters are fetched in non-deterministic thread order. Configure match_on=[method, uri] at both record and replay time before recording the cassette. Cannot be patched after the fact without re-recording.

5. **Two distinct circuit-breakers, not one counter** — A single consecutive_errors counter trips on 404s from deleted chapters and 403s on premium chapters. Fix: separate counters by error class — extraction-empty breaker, HTTP-wall breaker (429/5xx only), and 404 mapped to gone status with no breaker increment.

## Implications for Roadmap

Five phases are suggested. Ordering is driven by hard dependency constraints from architecture and pitfalls research.

### Phase A: Isolated Local Fixes

**Rationale:** Three changes touch exactly one file each with no pipeline dependencies. Safest first merge; establishes correctness before any invasive work.

**Delivers:** Comment recursion bounded; nh3 sanitization wired at extract-time; job/event memory caps in place.

**Implements:**
- api/comments.py: _parse_one() depth limit + warning
- scrape/chapter_html.py + pyproject.toml: nh3.clean() with explicit allowlist (img, br, data-p-id required)
- web/runner.py: JobManager._prune() + Job.events cap; prune-only-completed-jobs guard

**Avoids:** Pitfall 6 (img stripped from EPUB), Pitfall 7 (data-p-id stripped), Pitfall 12 (active SSE stream pruned), Pitfall 13 (comment truncation silent).

**Research flag:** Standard patterns. No additional research needed.

---

### Phase B: Auth Hardening

**Rationale:** Standalone new module; no pipeline changes; independent of parallelism. Eliminates the dead-cookie failure class.

**Delivers:** Cookie validation on startup (CLI + /setup); auth-failure detection mid-run; atomic config write.

**Implements:**
- New wattpad_crawler/auth.py: validate_cookie() + AuthError
- cli.py: validate before archive_story()
- web/routes.py: validate at /setup POST and job submit
- routes.py:_save_cookie(): atomic temp-path + os.replace()

**Avoids:** Pitfall 15 (public endpoint returning 200 for unauthenticated), Pitfall 16 (0-byte config on crash).

**Research flag:** Standard patterns. Verify cookie validation endpoint behavior manually with an expired cookie during implementation — Wattpad unofficial API is not documented.

---

### Phase C: Circuit-Breakers

**Rationale:** Must land after Phase A because extraction-empty breaker requires sanitization to be in place (empty content.text is only meaningful after nh3 runs). Simpler to implement and test in sequential mode before parallelism.

**Delivers:** Extraction-empty circuit-breaker; HTTP-wall circuit-breaker; 404 to gone mapping; configurable thresholds.

**Implements:**
- jobs.py: _Breaker dataclass + CircuitOpenError; two separate breaker instances in archive_story() part loop
- config.py: extraction_empty_threshold=3, http_wall_threshold=5

**Avoids:** Pitfall 9 (false positives on short/paywalled chapters), Pitfall 10 (404s tripping rate-limit breaker).

**Research flag:** Standard patterns. Custom in-process breaker definitively chosen; pybreaker/circuitbreaker explicitly rejected.

---

### Phase D: In-Story Parallelism

**Rationale:** Requires Phase C for clean abort-on-circuit-open under parallel execution. Requires SQLite thread-safety fixes. Most invasive phase — restructures the core archive_story() loop.

**Delivers:** workers_per_story actually controls concurrency; significant throughput improvement; correct failure surfacing under parallel execution; thundering-herd mitigation.

**Implements:**
- jobs.py: ThreadPoolExecutor(max_workers=cfg.workers_per_story); as_completed() with per-future try/except; threading.Lock on _Breaker._count
- archive/state.py:Manifest.connect(): check_same_thread=False + PRAGMA busy_timeout = 5000
- client.py: jitter on _sleep_backoff() so post-429 threads do not wake simultaneously

**Avoids:** Pitfall 1 (futures swallow exceptions), Pitfall 2 (thundering herd 429), Pitfall 3 (SQLite locked), Pitfall 14 (check_same_thread ProgrammingError), Pitfall 5 (__status__ SSE sentinel fires prematurely).

**Research flag:** No additional research needed. Implement the four sub-concerns (check_same_thread fix, busy_timeout, futures exception surfacing, jitter) incrementally with targeted tests for each.

---

### Phase E: Streaming Renders + Integration Test

**Rationale:** Cassette must be recorded against the final parallel pipeline to avoid re-recording. TXT/HTML streaming groups naturally with the test phase as a low-risk polish step.

**Delivers:** TXT and HTML renderers streaming (O(1) memory); EPUB atomic write (no partial corrupt .epub on crash); VCR cassette committed; integration test skip removed; pytest-recording migration complete.

**Implements:**
- render/txt.py, render/html.py: incremental write with shutil.copyfileobj; atomic temp-path + os.replace()
- render/epub.py: wrap epub.write_epub() in temp-path + os.replace(); document streaming limitation
- tests/integration/test_end_to_end.py: implement; configure match_on=[method, uri]; remove skip
- tests/fixtures/cassettes/: committed cassette YAML (manual offline recording step)
- pyproject.toml: remove pytest-vcr; add pytest-recording>=0.13.4; pin vcrpy>=8.1.1

**Avoids:** Pitfall 8 (EPUB partial ZIP corruption), Pitfall 11 (VCR cassette breaks under threaded fetches).

**Research flag:** Standard patterns. Cassette recording requires a live Wattpad network call. Story selection (2-3 chapters, public, stable) is an implementation decision.

---

### Phase Ordering Rationale

- Phase A before Phase C: sanitization must exist before extraction-empty breaker is meaningful
- Phase C before Phase D: breakers must work sequentially before being relied upon in parallel; CircuitOpenError propagation through the executor must be correct on first attempt
- Phase D before Phase E: cassette recorded against final parallel pipeline avoids re-recording
- Phase B is independent and can be parallelized with Phase A to reduce total wall-clock time

### Research Flags

All phases have standard patterns; no phase requires a dedicated research-phase call. The one implementation uncertainty (Wattpad auth endpoint behavior) is empirically resolvable with a 5-minute manual test during Phase B.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified on PyPI. bleach deprecation confirmed. pytest-vcr/pytest-recording incompatibility confirmed. |
| Features | HIGH | Features derived directly from codebase audit + PROJECT.md scope; all items traced to a specific silent-failure mode. |
| Architecture | HIGH | All findings from direct codebase inspection. Thread-safety analysis grounded in Python stdlib docs. |
| Pitfalls | HIGH | All 16 pitfalls grounded in direct codebase inspection. SQLite and concurrent.futures behaviors confirmed against Python docs. |

**Overall confidence: HIGH**

### Gaps to Address

- **Wattpad library endpoint auth behavior**: Use GET /api/v3/users/{username}/library?limit=1 as the auth probe; verify manually with an expired cookie during Phase B that it returns non-200 or an error body. Check response body for an id or items field, not just HTTP status.

- **EPUB streaming deferral**: ebooklib has no incremental API — confirmed. Known limitation documented. If OOM is observed on 500+ chapter stories, the escape path is a custom ZipFile serializer, out of scope for this milestone.

- **VCR cassette story selection**: Pick a small public Wattpad story (2-3 chapters, at least one inline comment, unlikely to be deleted) during Phase E. No research blocker; this is an implementation decision.

- **Optimal workers_per_story default**: The recommended default of 3 workers is a starting point. The HTTP-wall circuit-breaker provides the safety net if the rate is too aggressive.

## Sources

### Primary (HIGH confidence — verified)
- PyPI nh3 0.3.5: https://pypi.org/project/nh3/
- PyPI bleach 6.3.0 (deprecated): https://pypi.org/project/bleach/
- PyPI vcrpy 8.1.1: https://pypi.org/project/vcrpy/
- PyPI pytest-recording 0.13.4: https://pypi.org/project/pytest-recording/
- vcrpy changelog (httpcore rewrite): https://vcrpy.readthedocs.io/en/latest/changelog.html
- nh3 API docs: https://nh3.readthedocs.io/en/latest/
- Codebase: wattpad_crawler/client.py, archive/state.py, jobs.py, web/runner.py, api/comments.py, render/epub.py, scrape/chapter_html.py
- Python stdlib sqlite3 docs — check_same_thread, busy_timeout
- Python stdlib concurrent.futures docs — future.result() exception behavior

### Secondary (MEDIUM confidence — community consensus + reverse-engineering)
- Wattpad API v3 library endpoint auth behavior — reverse-engineered from api/user.py + community Wattpad wrapper patterns
- workers_per_story default of 3 — heuristic; not empirically validated against live Wattpad rate limits
- yt-dlp, gallery-dl, Scrapy pattern survey: https://github.com/yt-dlp/yt-dlp, gallery-dl docs, https://docs.scrapy.org/

### Tertiary (LOW confidence — inference)
- EPUB OOM threshold (~300-500 chapters) — heuristic from renderer code analysis; no profiling data
- Wattpad session cookie lifetime — community-reported; not officially documented

---
*Research completed: 2026-05-03*
*Ready for roadmap: yes*
