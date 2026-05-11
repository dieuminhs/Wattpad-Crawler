# Feature Research: Hardening Milestone

**Domain:** Personal Python scraper/archiver — hardening, not new features
**Researched:** 2026-05-03
**Milestone scope:** Harden v1 — long-run reliability, silence elimination, controlled parallelism
**Confidence:** HIGH (architecture deeply known from audit; patterns confirmed against yt-dlp, gallery-dl, Scrapy, vcrpy ecosystem)

---

## Framing: What "Hardening" Means Here

The user's worst case is "spent the night archiving and got empty files — only discovered in the morning." Every feature below is evaluated against whether it kills one of these silent-failure modes or makes failures faster and louder to diagnose. Features that add surface area, complexity, or require new user habits without clear reliability payoff are anti-features for this milestone.

Reference tools surveyed: yt-dlp (batch download CLI), gallery-dl (image archiver), Scrapy (framework), vcrpy/pytest-recording (cassette testing).

---

## Table Stakes (Missing = Milestone Falls Short)

Features that a solo user running overnight archives will notice the absence of immediately.

| Feature | Why Expected | Complexity | Notes / Dependency |
|---------|--------------|------------|-------------------|
| **Cookie validation on startup** | Without this, a dead cookie is discovered hours into a run when chapters are already empty stubs. Gallery-dl treats auth failure as a blocking error, not a warning. yt-dlp surfaces "Sign in to confirm" immediately. | S | Quick API probe (`/users/me` or story fetch) before any archive work. Blocks job start on hard fail. Needs: RateLimitedClient already exists. |
| **Auth-failure detection mid-run** | Even with a good cookie at start, Wattpad can expire a session mid-run. A 403 or "login-required" JSON shape in a chapter response should surface as a distinct error class, not fall through to "empty chapter." | S | Pattern: gallery-dl raises `AuthenticationError`; yt-dlp emits "Sign in required" and stops. Classify HTTP 403 + session-expired body separately from generic 4xx. Needs: cookie validation feature above. |
| **Extraction integrity check (circuit-breaker on empty chapters)** | If the `data-p-id` selector breaks, every future chapter silently archives as empty. The current `WARNING` log is not enough. Mature scrapers (Scrapy's `CLOSESPIDER_ERRORCOUNT`) stop rather than continue producing garbage. | M | Heuristic: if raw HTML > N bytes but extracted text < M chars, mark part "failed" with an explicit reason, not "done." After K consecutive such failures (e.g., 3), pause the job loudly — emit a distinct event, log prominently, set job status "paused:extraction_broken". Does not auto-resume; user must investigate. |
| **HTTP error circuit-breaker (4xx/5xx cap)** | Unlimited retries on 429 + 5xx can loop forever burning API quota. Gallery-dl has `extractor.*.retries` (default: 4) and `sleep-retries` with exponential backoff. yt-dlp has `--retries` (default: 10). Neither loops without bound. | S | Add `max_consecutive_errors` (configurable, default 10). On breach: set job status "paused:upstream_errors", emit an event with the last HTTP status, stop the chapter loop. User restarts the run, which re-enters at the first non-done part. |
| **Bounded comment-reply recursion** | A malformed API response with circular or very deep replies will stack-overflow. No mature scraper recurses infinitely on API data. | S | Add `max_comment_depth` parameter (default: 10). At limit, flatten remaining replies with a flag field `truncated=True`. No user-visible UI needed — just correct behavior. |
| **Failure summary at job end (CLI + Web)** | yt-dlp prints "N errors" at the end of a playlist run. Gallery-dl summarizes failed URLs. Without a summary, users must grep logs. For a 500-chapter story, knowing "47 parts failed" is the first signal something systematic went wrong. | S | At job completion, collect failed parts from Manifest (`status = 'failed'`). Emit a "job.summary" event with counts: `{done, skipped, failed, extraction_errors}`. In CLI mode, print this as a final status block. In web UI, show it on the job detail page alongside the event stream. |
| **Render failure surfaced loudly** | Render failures are currently logged and silently skipped. If all three renderers (TXT, HTML, EPUB) fail, the user has no outputs but the job shows "done." | S | If all three renderers fail for a story, set job status "failed" (not "done"). Emit a distinct "render.all_failed" event. Partial failures (one format fails) are fine to continue, but must appear in the job summary. |
| **Job event list cap** | `Job.events` grows without bound. A 500-chapter archive at ~5 events/chapter = 2,500+ events per job, held in memory indefinitely. Long web sessions accumulate this across multiple jobs. | S | Cap `Job.events` at last N events (e.g., 500). On overflow, drop oldest (or mark oldest as "truncated"). Needs no user-visible change — existing SSE stream replays from the list. |
| **JobManager history pruning** | `JobManager._jobs` and `._order` grow forever. After enough jobs the dashboard gets slow and memory grows. | S | Keep only last N jobs (e.g., 50). On `create()`, if over limit, evict the oldest finished job. Active jobs are never evicted. |
| **In-story chapter parallelism (workers_per_story)** | Config key exists, is documented, but is not wired. Users who tweak it expect it to do something. More concretely: sequential chapter fetching on a 300-chapter story is the headline time sink. Mature tools (yt-dlp `--concurrent-fragments`, gallery-dl implicit per-extractor concurrency) make concurrency the default. | M | Wire `workers_per_story` to a `ThreadPoolExecutor` scoped to one story's chapter fetch phase. All threads share the existing `RateLimitedClient` token bucket (already thread-safe). Render phase stays sequential (memory concern). Default workers: 3. Needs: token-bucket thread-safety verified (it is, per audit). |
| **HTML sanitization before storage (nh3)** | Raw paragraph HTML from Wattpad is stored verbatim and rendered into EPUB/HTML output. If any story contains injected HTML/JS, it replicates into the archive. `nh3` (Rust-backed, Bleach is deprecated since Jan 2023) is the ecosystem-standard choice for exactly this use case. | S | Apply `nh3.clean()` with an EPUB-safe allow-list (block, inline content tags; no `<script>`, `<style>`, `<iframe>`; allow `src` on `<img>`) to paragraph HTML before writing to JSON. Single call site in `scrape/chapter_html.py`. Dep: `nh3`. |
| **Streamed HTML/TXT rendering** | Renderers accumulate all chapters in memory before writing. For large stories this can hit 50-100 MB. The fix is trivially cheap for TXT and HTML: open the output file at the start, write each chapter as it's read from disk, close at the end. | S | For `render_txt` and `render_html`: replace the accumulate-then-write pattern with an incremental write loop. EPUB requires more care (ebooklib design); defer EPUB streaming until profiled. Needs: no new deps. |
| **VCR integration test (canary story)** | `tests/integration/test_end_to_end.py` exists but is `pytest.mark.skip` because no cassette is recorded. Without it, API breakage is discovered only by running the real tool. The standard pattern (vcrpy `--record-mode=once` + `--vcr-record=none` in CI) is well-established. | M | Pick a tiny public story (1-2 chapters). Record cassette with `--record-mode=once` + `filter_headers=['cookie']`. Commit YAML cassette. Remove `pytest.mark.skip`. Assert: metadata.json present, at least one `.txt` part, at least one `.epub` output, no parts with `status='failed'` in Manifest. Add `--vcr-record=none` to CI config. |

---

## Differentiators (Nice, Cheap to Include; Otherwise Defer)

Features not expected by a solo user but worth including if implementation is shallow.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Comments pagination cap warning** | Currently silently truncates at 200 pages (20k comments). Logging `WARNING: comment page cap reached for part {id}` adds zero complexity but surfaces a real data-completeness signal. | S | One `logger.warning()` call in `api/comments.py` when `_MAX_PAGES` is hit. No config change needed. |
| **Cover fetch: distinguish "no cover" vs "fetch failed"** | Library grid currently shows the same gray placeholder for both. Tracking `cover_status` in Manifest (values: `none`, `ok`, `failed`) would let the UI show a small error badge. | S | Needs one new Manifest column. If a migration script is needed, that makes it M. |
| **Config unknown-key warning** | If user typos `rate_limit_pers_sec`, it silently uses the default. One `logger.warning()` for unexpected TOML keys costs nothing but prevents confusion. | S | Collect known keys; diff against parsed dict. Single pass in `config.py`. |
| **`workers_per_story` TTY feedback** | When parallelism is active, print/emit something like "Fetching N chapters with K workers" so the user knows the setting is live. | S | One `logger.info()` at the start of the parallel fetch. |
| **Extraction integrity threshold as config** | The heuristic thresholds (raw HTML byte floor, extracted text char floor, consecutive-failure cap) shouldn't be hardcoded magic numbers. Expose as optional TOML keys with documented defaults. | S | Add three keys to `Config` with defaults. Zero new behavior, just flexibility for tuning. |

---

## Anti-Features (Explicitly Rejected for This Milestone)

Patterns that arise naturally when hardening a scraper but are wrong scope here.

| Anti-Feature | Why Requested | Why Wrong Here | Better Approach |
|--------------|---------------|----------------|-----------------|
| **Circuit-breaker auto-resume / half-open state** | Scrapy and enterprise circuit-breakers support half-open state (probe after timeout, resume if probe passes). | This is single-user, single-machine. Auto-resume requires background scheduling logic, timer state, and re-entry coordination that adds real complexity. The user is present; they can restart. The value of auto-resume is near zero vs. the complexity cost. | Pause loudly with a clear message ("Paused after 10 consecutive 429s. Restart the run to resume from the last successful part."). User restarts; Manifest already tracks what's done. |
| **Pause/resume UI in web dashboard** | Scrapy supports `Ctrl-C` to pause; some web archivers have a Pause button. | Adding a "Pause" button to the web job UI requires threading coordination (interrupt a running ThreadPoolExecutor gracefully), web route changes, and new job state machine states. Out of scope for a hardening milestone. | Circuit-breaker already pauses the job; user sees the paused state on the job detail page. That is sufficient. |
| **Persistent job history to SQLite** | Long-running tools often persist run history to survive restarts. | The PROJECT.md explicitly defers this. The real state is already in the Manifest + filesystem. In-memory job history cap (Table Stakes, above) solves the bloat concern without schema work. | Implement memory cap. Document that web job history is ephemeral. If a user needs history, they consult Manifest `status` table. |
| **Multi-story parallelism (archive_many concurrent)** | Batch mode could run 3 stories concurrently. | Current design runs one `JobRunner` thread per submitted web job, already providing story-level concurrency. Adding pool-within-pool with shared rate budget is a significant concurrency design change. `workers_per_story` + a sensible rate limit is sufficient speedup. | `workers_per_story` within one story. Document that launching multiple web jobs achieves story-level parallelism for batch use. |
| **EPUB streaming via ebooklib incremental API** | Render memory is an issue for very large stories. | ebooklib's design doesn't expose a clean incremental API; the workaround requires writing per-chapter intermediate files, which is real R&D work. The PROJECT.md already says "profile first." | Do TXT + HTML streaming (cheap, Table Stakes). Defer EPUB streaming until a real OOM is observed and measured. |
| **Memory-usage monitoring / metrics dashboard** | Enterprise scrapers emit memory metrics. | Explicitly out of scope in PROJECT.md. Overkill for a personal tool. | Streaming renders (Table Stakes) address the root cause. No metrics dashboard needed. |
| **HTTPS certificate pinning** | Hardening a scraper might include pinning Wattpad's cert. | Personal use only. Would break on Wattpad cert rotation and add maintenance burden. | Accept default httpx TLS verification. |
| **Rate limit randomization / jitter beyond token bucket** | Scrapers often add random jitter to appear less bot-like. | While valid, this is ToS-compliance / evasion work, explicitly not in scope for a hardening milestone focused on reliability. | Document as a future enhancement in the Wattpad ToS section. |
| **New web UI pages / reader features** | Easy to slip in while touching routes. | Explicit project constraint: no new end-user features. | Defer to Features v2. |
| **Multi-account / multi-cookie support** | Some tools manage multiple auth sessions. | Single-user personal tool. Adds config complexity with no payback. | One cookie in `_config.toml`. |

---

## Feature Dependencies

```
[Cookie validation on startup]
    └──requires──> validates before job launches
    └──enables──> [Auth-failure detection mid-run] (shares the "what does auth failure look like" logic)

[Extraction integrity check]
    └──requires──> [HTTP error circuit-breaker] concept (both use consecutive-failure counting)
    └──enables──> [Failure summary at job end] (contributes extraction_error counts)

[In-story chapter parallelism]
    └──requires──> token-bucket thread-safety (already confirmed)
    └──requires──> consistent part ordering for EPUB (chapters must be assembled in ordinal order, not completion order)
    └──enables──> [Streamed HTML/TXT rendering] becomes more important (parallel fetches produce parts faster, sequential render is now proportionally more of the wall time)

[Failure summary at job end]
    └──requires──> Manifest `status='failed'` already tracked (already exists)
    └──requires──> [Render failure surfaced loudly] (render failures must also be counted in summary)

[VCR integration test]
    └──requires──> cassette recorded once manually (offline step)
    └──requires──> pytest-recording or pytest-vcr in dev deps
    └──enables──> detection of [Extraction integrity check] regressions and [Cookie validation] flow
    └──enhances──> [Extraction integrity check] (cassette can include a chapter with large raw HTML + failing selector, asserting the circuit-breaker fires)

[HTML sanitization (nh3)]
    └──requires──> dep: nh3 (no conflicts with existing deps)
    └──no conflicts with parallelism (nh3 is pure function, stateless, thread-safe)

[Job event list cap] ──enables──> [JobManager history pruning] (together they fully bound web UI memory)

[HTTP error circuit-breaker]
    └──requires──> distinct error event type in Job.events
    └──enables──> [Failure summary] (circuit-break counts as a failure reason)
```

### Dependency Notes

- **Parallelism requires ordinal-ordered assembly:** `ThreadPoolExecutor.map()` with ordered iteration preserves submission order in results. Use `executor.map()` over chapter list, not `submit()` + `as_completed()`, so chapters are processed in ordinal order for the render phase.
- **Auth detection before vs. during run:** Cookie validation on startup is a fast-path gate. Mid-run auth detection is a separate concern (the cookie could expire during a 6-hour run). Both are needed; neither replaces the other.
- **Extraction circuit-breaker vs. HTTP circuit-breaker:** These are separate counters. A Wattpad-side HTML restructure produces 200 OK responses with large HTML but zero extracted paragraphs (extraction failure). A rate-limit ban produces 429s (HTTP failure). Both should trigger pausing, but they diagnose different root causes and should emit different events.

---

## MVP Definition for This Milestone

### Must Ship (Harden v1 is incomplete without these)

- [ ] Cookie validation on startup (CLI + /setup) — kills the "dead cookie, 6 hours wasted" failure class
- [ ] Extraction integrity check / circuit-breaker — kills the "selector broke, 300 empty chapters" failure class
- [ ] HTTP error circuit-breaker (max consecutive errors, no infinite loops) — kills the "looping on 429 forever" failure class
- [ ] Bounded comment recursion — kills the stack-overflow / runaway failure class
- [ ] HTML sanitization via nh3 — kills the stored-XSS and EPUB-corruption class
- [ ] Failure summary at job end (CLI + web) — makes failure visible without log-grep
- [ ] Render failure surfaced loudly (all renderers failed = job failed) — makes render failure visible
- [ ] Job event list cap + JobManager history pruning — kills memory growth for long web sessions
- [ ] In-story chapter parallelism (workers_per_story wired) — makes the config key real; primary speed win
- [ ] Streamed HTML/TXT rendering — kills the 50-100MB memory accumulation per story
- [ ] VCR integration test (canary story, cassette committed, skip removed) — makes API breakage detectable in CI

### Include If Cheap (Differentiators — All Are S Complexity)

- [ ] Comments pagination cap warning (one logger.warning call)
- [ ] Config unknown-key warning (one pass in config.py)
- [ ] workers_per_story TTY feedback (one logger.info)
- [ ] Extraction integrity thresholds exposed as config keys

### Defer to Future Milestone

- [ ] Cover fetch cover_status tracking in Manifest (needs migration; value is cosmetic)
- [ ] EPUB streaming rendering (profile first; no OOM observed yet)
- [ ] Circuit-breaker auto-resume / half-open state (single-user, manual restart is fine)
- [ ] Persistent job history to SQLite (memory cap is sufficient for solo use)

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Cookie validation on startup | HIGH | LOW (S) | P1 |
| Extraction integrity check | HIGH | MEDIUM (M) | P1 |
| HTTP error circuit-breaker | HIGH | LOW (S) | P1 |
| Bounded comment recursion | HIGH | LOW (S) | P1 |
| HTML sanitization (nh3) | HIGH | LOW (S) | P1 |
| Failure summary at job end | HIGH | LOW (S) | P1 |
| Render failure surfaced loudly | HIGH | LOW (S) | P1 |
| In-story chapter parallelism | HIGH | MEDIUM (M) | P1 |
| Streamed HTML/TXT rendering | MEDIUM | LOW (S) | P1 |
| VCR integration test | HIGH | MEDIUM (M) | P1 |
| Job event list cap | MEDIUM | LOW (S) | P1 |
| JobManager history pruning | MEDIUM | LOW (S) | P1 |
| Comments pagination cap warning | LOW | LOW (S) | P2 |
| Config unknown-key warning | LOW | LOW (S) | P2 |
| workers_per_story TTY feedback | LOW | LOW (S) | P2 |
| Extraction thresholds in config | LOW | LOW (S) | P2 |
| Cover fetch status tracking | LOW | MEDIUM (M) | P3 |
| EPUB streaming rendering | MEDIUM | HIGH (L) | P3 |
| Circuit-breaker auto-resume | LOW | HIGH (L) | P3 |
| Persistent job history | LOW | MEDIUM (M) | P3 |

---

## Patterns Observed in Reference Tools

### yt-dlp

- Progress: `[download] 38.6% of 25.97MiB at 2.79MiB/s ETA 00:05` — item-level with ETA. For batch (playlist), tracks N/M items.
- Failure UX: Continues on individual item failure, prints `ERROR:` in red per item. At the end of a playlist run, reports total count of errors. Does NOT have a clean "write failed URLs to file" built-in (active feature request as of 2026).
- Retry: `--retries N` (default 10), `--fragment-retries N`. Exponential backoff. Does not loop infinitely.
- Cookie UX: `--cookies` or `--cookies-from-browser`. Validates immediately; if cookies are rejected, emits a clear "Sign in to confirm you're not a bot" error and stops.
- Concurrency: `--concurrent-fragments N` for within-item parallelism (segment download). No per-playlist story-level concurrency built-in.

### gallery-dl

- Auth: Validates at extractor init. `AuthenticationError` raised immediately on bad credentials — job never starts content download with bad auth.
- Rate limiting: `extractor.*.retries` (default 4), `sleep-retries` with `exp=N` exponential backoff, `sleep-429` (default: 60s). Bounded, not infinite.
- Skip strategy: `skip: "abort:5"` stops after 5 consecutive skips — useful pattern for "already archived" detection.
- Archive: SQLite-backed download archive, same idea as our Manifest.
- No built-in circuit-breaker UI; failures surface as exceptions that terminate the run.

### Scrapy

- Pause/resume: CLI-level via JOBDIR; state stored as scheduler queue on disk. User re-runs identical command to resume. No web UI for pause/resume.
- Error count: `CLOSESPIDER_ERRORCOUNT` closes the spider after N item errors. This is the circuit-breaker equivalent for Scrapy.
- Progress: No built-in progress bar; users add `scrapy-progress` extensions or custom stats logging.

### vcrpy / pytest-recording

- Standard pattern: `@pytest.mark.vcr` decorator, cassette recorded once with `--record-mode=once`, replayed in CI with `--vcr-record=none`.
- Secret management: `filter_headers=['cookie', 'authorization']` prevents credentials leaking into cassette YAML.
- Cassette format: YAML with full request/response pairs. Human-readable enough to audit before committing.
- Canary test pattern: Assert structural invariants (files exist, status in Manifest is 'done', no empty `.txt` parts) rather than exact content. This catches API shape changes without being brittle to content changes.

### nh3 (HTML sanitization)

- Replaces deprecated bleach (deprecated Jan 2023; underlying html5lib unmaintained).
- ~20x faster than bleach (Rust-backed via Ammonia).
- `nh3.clean(html, tags={...}, attributes={...})` is the core API. Stateless, thread-safe.
- No EPUB-specific preset; must define an allow-list manually. Appropriate list for reading content: `{p, em, strong, b, i, u, s, br, a, img, span, div, blockquote, ul, ol, li, hr}` with `img[src,alt]` and `a[href]`.
- No `<script>`, `<style>`, `<iframe>`, `<form>` permitted.

---

## Sources

- yt-dlp source and issues: https://github.com/yt-dlp/yt-dlp
- gallery-dl configuration docs: https://manpages.debian.org/testing/gallery-dl/gallery-dl.1.en.html
- gallery-dl raw config rst: https://raw.githubusercontent.com/mikf/gallery-dl/master/docs/configuration.rst
- Scrapy jobs/pause/resume: https://docs.scrapy.org/en/latest/topics/jobs.html
- Scrapy retry middleware: https://webscraping.ai/faq/scrapy/how-do-i-implement-retry-logic-in-scrapy
- vcrpy documentation: https://vcrpy.readthedocs.io/
- pytest-recording: https://github.com/kiwicom/pytest-recording
- vcrpy blog post (2025): https://alexwlchan.net/2025/testing-with-vcrpy/
- vcrpy web scraper testing (2025): https://datawookie.dev/blog/2025-01-28-test-a-web-scraper-using-vcr/
- nh3 documentation: https://nh3.readthedocs.io/
- nh3 vs bleach comparison: https://adamj.eu/tech/2023/12/13/django-sanitize-incoming-html-nh3/
- bleach deprecation: https://github.com/mozilla/bleach
- nh3 PyPI: https://pypi.org/project/nh3/
- scrapfly failover strategies: https://scrapfly.io/blog/posts/automatic-failover-strategies-for-reliable-data-extraction
- yt-dlp feature request (failed URL log): https://github.com/yt-dlp/yt-dlp/issues/7832
- yt-dlp concurrent-fragments design: https://deepwiki.com/boul2gom/yt-dlp/6.2-parallel-segmentation

---

*Feature research for: Wattpad Crawler — Harden v1 milestone*
*Researched: 2026-05-03*
