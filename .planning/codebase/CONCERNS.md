# Codebase Concerns

**Analysis Date:** 2026-05-03

## Legal & Terms of Service

**Wattpad scraping inherent risk:**
- Issue: The tool directly scrapers Wattpad's unofficial API and HTML, violating Wattpad's Terms of Service
- Files: `wattpad_crawler/api/`, `wattpad_crawler/scrape/chapter_html.py`
- Impact: User accounts can be permanently banned; IP addresses can be blocklisted; Wattpad can change endpoints/HTML structure without notice, breaking the tool
- Current mitigation: User-configurable rate limiting (`rate_limit_per_sec`, default 2.0 req/sec) to reduce detection risk. Documentation warns in README
- Recommendations: 
  - Prominently display in CLI/Web UI that this violates Wattpad ToS
  - Advise users to only archive content they have permission to download
  - Consider adding delays/randomization beyond token bucket
  - Document breakage recovery workflow

## Technical Debt

**Brittle CSS selector for chapter content extraction:**
- Issue: Chapter parsing relies on `.page-container` selector and `data-p-id` attribute, both of which are internal implementation details subject to change
- Files: `wattpad_crawler/scrape/chapter_html.py:18`, `wattpad_crawler/scrape/chapter_html.py:27`
- Impact: If Wattpad changes their HTML structure (classname, attribute names), all future chapter downloads will fail silently with empty content
- Current mitigation: Warning log when no `data-p-id` elements found, but doesn't stop the archive
- Recommendations:
  - Add circuit-breaker logic: if 3+ consecutive chapters return zero paragraphs, pause and error loudly
  - Consider scraping alternative data source (e.g., JSON API if available)
  - Add integration tests with real public stories to detect breakage early

**Undocumented rate-limit detection gaps:**
- Issue: Tool respects HTTP 429 responses and Retry-After headers, but Wattpad may use other anti-bot signals (IP throttling, IP bans, session invalidation) that the tool cannot detect
- Files: `wattpad_crawler/client.py:73-106`
- Impact: Tool may continue failing silently while appearing to work, making large archive attempts waste time before hitting a silent ban
- Current mitigation: User can manually lower `rate_limit_per_sec` in config
- Recommendations:
  - Add exponential backoff with max retry count (currently unlimited retries on 5xx)
  - Add circuit-breaker: if 10+ consecutive requests timeout, auto-pause and alert user
  - Log all HTTP errors to help users detect IP bans early

**Cookie expiration not enforced:**
- Issue: Session cookies expire periodically, but the tool does not check expiration or validate auth status before starting a large archive
- Files: `wattpad_crawler/client.py:13-20`, `wattpad_crawler/web/routes.py:54-76`
- Impact: A user can start archiving a library of 1000 stories, encounter a dead cookie mid-way, and only discover the failure hours later when all chapters are empty stubs
- Current mitigation: None explicit; user must manually detect when login-required content fails
- Recommendations:
  - On startup, validate cookie with a quick test API call
  - Add cookie validation endpoint to web UI (/setup) that tests auth before saving
  - Log a warning if any response suggests auth failure (403, "login required" messages)

## Scraping Fragility

**Paragraph HTML stored without sanitization; potential XSS on re-render:**
- Issue: `extract_chapter()` calls `para.decode_contents()` which returns raw HTML from paragraph elements, stored verbatim in JSON and HTML/EPUB output
- Files: `wattpad_crawler/scrape/chapter_html.py:44`, `wattpad_crawler/archive/store.py:128`, `wattpad_crawler/render/html.py:33`
- Impact: If Wattpad user content includes malicious HTML/JS (e.g., via comment injection or stored XSS in platform), it gets replicated into the archive and could execute when EPUB/HTML is opened in a reader app
- Current mitigation: 
  - Web reader renders chapter body as plain text in `<pre>` tag, preventing execution
  - HTML render uses `html.escape()` for metadata but NOT for `chapter_html` raw content
- Recommendations:
  - Sanitize paragraph HTML before storing: use `bleach` or `nh3` to strip/escape dangerous tags
  - Document that untrusted EPUB/HTML files should only be opened in sandboxed readers
  - Consider extracting only text from paragraphs (already done in `.txt` output)

**Cover image fetch not validated for size/type:**
- Issue: Cover image fetched and stored without checking content-type or size limit
- Files: `wattpad_crawler/jobs.py:87-93`
- Impact: Malicious or corrupted server could send gigabyte files or non-image content, filling disk and crashing the tool
- Current mitigation: Cover fetch failures are caught and logged, not fatal; empty cover bytes are no-op
- Recommendations:
  - Add file size limit (e.g., max 5MB for cover)
  - Validate content-type is image/* before saving
  - Add timeout to cover fetches (currently inherits from global timeout)

**Comment recursion unbounded:**
- Issue: Comments can have nested replies, recursively parsed without depth limit
- Files: `wattpad_crawler/api/comments.py:20-25`
- Impact: A deeply nested reply thread (or malformed API response) could cause stack overflow or memory exhaustion
- Current mitigation: None
- Recommendations:
  - Add max depth limit (e.g., 10 levels deep)
  - Flatten deep replies after depth limit with a warning

**Missing API response validation:**
- Issue: API responses parsed with minimal validation; missing `id` fields handled defensively in some places but not all
- Files: `wattpad_crawler/api/story.py:13-52`, `wattpad_crawler/api/comments.py:12-43`
- Impact: Malformed API responses can silently skip parts/comments or raise hard errors mid-archive
- Current mitigation: Explicit None checks for `id` fields in story/comment parsing
- Recommendations:
  - Validate required fields at parse time and raise descriptive errors
  - Add debug logging of raw API responses (in verbose mode) for debugging

## Performance & Scaling Concerns

**In-memory rendering of entire stories:**
- Issue: `render_html()`, `render_txt()`, and `render_epub()` load all chapter files into memory and concatenate before writing
- Files: `wattpad_crawler/render/html.py:17-45`, `wattpad_crawler/render/txt.py:7-32`, `wattpad_crawler/render/epub.py:7-51`
- Impact: A 50-chapter story with inline comments can easily reach 50-100MB in memory; very large stories (500+ chapters) could cause OOM
- Current mitigation: None; renders happen one-at-a-time in main thread
- Recommendations:
  - Use streaming/chunked writes for HTML and TXT (write directly to file instead of accumulating)
  - For EPUB, consider using ebooklib's incremental API or writing chapters to intermediate files
  - Add memory-usage monitoring and skip render if <100MB free

**All jobs run in serial, blocking each other:**
- Issue: `archive_many()` processes stories sequentially, and web UI submits only one job at a time despite config having `workers_per_story` setting
- Files: `wattpad_crawler/jobs.py:173-196`, `wattpad_crawler/web/routes.py:113-144`
- Impact: Archiving a 500-story library takes linear time (500 * avg_story_time). Cannot parallelize even though system has CPU cores
- Current mitigation: `workers_per_story` config exists but is unused; token bucket rate limits are per-client
- Recommendations:
  - Implement per-story thread pooling (use `concurrent.futures.ThreadPoolExecutor`)
  - Make `workers_per_story` actually control chapter fetch parallelism within one story
  - Document that chapter fetches can run in parallel but respect global rate limit

**Web server holds unbounded job history in memory:**
- Issue: `JobManager` stores all jobs ever created in `_jobs` dict and `_order` list, never purged
- Files: `wattpad_crawler/web/runner.py:72-94`
- Impact: Long-running web UI server can accumulate thousands of jobs, consuming RAM and slowing list operations
- Current mitigation: Web UI displays only last 10 jobs
- Recommendations:
  - Implement job history pruning: delete jobs older than 7 days or keep only last 1000
  - Persist completed jobs to SQLite archive if historical tracking is desired
  - Add memory metrics to dashboard

**Comments pagination caps at 200 pages without indication:**
- Issue: Comment fetching stops at `_MAX_PAGES=200` pages (20k comments per chapter) silently
- Files: `wattpad_crawler/api/comments.py:9`, `wattpad_crawler/api/comments.py:50`
- Impact: Very popular chapters with 20k+ comments are silently truncated, losing data without user awareness
- Current mitigation: Max page is documented in code but not logged
- Recommendations:
  - Log a warning if pagination hits the limit
  - Expose cap as config setting
  - Consider increasing cap or removing it if test stories show feasibility

## Concurrency & Data Integrity

**SQLite WAL mode enables reader-writer concurrency but journal is not synced to disk:**
- Issue: `state.py:63` enables SQLite WAL mode and atomic writes use os.replace (no fsync), so power loss after a write returns may still lose the most recent transaction
- Files: `wattpad_crawler/archive/state.py:57-68`, `wattpad_crawler/archive/store.py:54-71`
- Impact: Rare but possible data corruption after sudden power loss (power outage, kernel panic, hardware failure)
- Current mitigation: Documented in code comments as acceptable for personal archive tool
- Recommendations:
  - Accept this limitation (reasonable for personal use case)
  - Add fsync-on-critical-path option for users on unreliable hardware (e.g., USB drives)

**File write collisions if multiple archive processes run concurrently:**
- Issue: `_tmp_path()` uses `os.getpid()` and `threading.get_ident()` but same PID+thread can collide if files are written in rapid succession
- Files: `wattpad_crawler/archive/store.py:47-51`
- Impact: If two processes write the same story dir simultaneously, final file may be from either writer (race condition)
- Current mitigation: Web UI runs only one job at a time; CLI is single-threaded
- Recommendations:
  - Add UUID to tmp filename instead of just PID+thread
  - Document that only one CLI instance per output directory is safe

**Job events list grows unbounded per job:**
- Issue: `Job.events` list appends every progress update without cap
- Files: `wattpad_crawler/web/runner.py:38-41`
- Impact: A long-running archive job can accumulate thousands of events, consuming RAM
- Current mitigation: None
- Recommendations:
  - Cap events list at last 1000 or expire events older than 1 hour
  - Persist events to SQLite if historical logging is needed

## Input Validation & Security

**Path traversal protection is in place but relies on resolve():**
- Issue: Web routes use `Path.resolve()` and `is_relative_to()` to prevent path traversal, which is correct but Windows paths may have edge cases
- Files: `wattpad_crawler/web/routes.py:205-212`, `wattpad_crawler/web/routes.py:215-224`
- Impact: Unlikely but possible bypass if symlinks or UNC paths are involved
- Current mitigation: Good baseline validation in place
- Recommendations:
  - Add tests for symlink escape attempts
  - Consider additional validation: reject `..` literals even after resolve()

**No validation that requested chapters exist before rendering them:**
- Issue: `reader_chapter()` reads `metadata.json` and assumes ordinals match file naming, but doesn't validate part files exist before reading
- Files: `wattpad_crawler/web/routes.py:248-281`
- Impact: If part files are missing or corrupted, chapter reader returns 200 with error message instead of 404
- Current mitigation: Fallback to "(missing chapter body)" message
- Recommendations:
  - Check part file exists before rendering; return 404 if missing
  - Log missing parts as potential corruption

**Username/list_id not validated before API calls:**
- Issue: Web form accepts username and list_id without regex validation, passed directly to API
- Files: `wattpad_crawler/web/routes.py:129-140`
- Impact: Invalid usernames/IDs cause API errors, but no client-side validation message
- Current mitigation: API returns error, user sees generic HTTP 500 or job failure
- Recommendations:
  - Add basic validation: username must be alphanumeric+underscore, list_id must be numeric
  - Return 400 with descriptive message instead of letting API error bubble

## Error Handling & Observability

**Failed renders are silently skipped:**
- Issue: Render failures (EPUB, HTML, TXT) are logged but don't fail the job or alert user
- Files: `wattpad_crawler/jobs.py:133-142`
- Impact: User thinks story is fully archived but output files are missing, only discovered later
- Current mitigation: Emit "render.failed" progress event, logged in job
- Recommendations:
  - Make at least one render format mandatory; fail job if all renders fail
  - Highlight render failures in job UI with error badge

**Cover fetch failures are silent:**
- Issue: Cover image fetch errors are caught and logged, but missing covers are not visible in UI
- Files: `wattpad_crawler/jobs.py:87-93`
- Impact: Users don't know if a story has a cover or if the fetch failed
- Current mitigation: UI falls back to gray placeholder
- Recommendations:
  - Track cover fetch status in manifest; distinguish "missing" from "failed"
  - Display a warning badge if cover fetch failed (user can retry)

**Part body text extraction failure is not detected:**
- Issue: If `extract_chapter()` returns empty `text` (e.g., due to selector changes), this is silently archived and no warning emitted
- Files: `wattpad_crawler/scrape/chapter_html.py:16-47`
- Impact: Empty chapters silently archived as valid; users don't know until reading the story
- Current mitigation: Log warning if no `data-p-id` elements found, but doesn't fail the job
- Recommendations:
  - Fail the part with a clear error if extracted text is empty AND raw_html is substantial
  - Add heuristic: if raw_html > 5KB but extracted text < 100 chars, flag as likely extraction failure

## Deployment & Configuration

**Config file can be corrupted by concurrent writes:**
- Issue: `_save_cookie()` reads entire config, modifies in memory, writes back; no lock prevents concurrent updates
- Files: `wattpad_crawler/web/routes.py:22-45`
- Impact: If user updates cookie in web UI while CLI is also writing state, config may be corrupted
- Current mitigation: Single web server instance and single CLI instance per archive
- Recommendations:
  - Use atomic write pattern (write to temp, then rename)
  - Add file lock around config reads/writes

**Config parsing silently defaults missing values:**
- Issue: `load_config()` uses `.get()` with defaults for all settings, so typos in config silently become defaults
- Files: `wattpad_crawler/config.py:40-47`
- Impact: User types `rate_limit_per_sec = abc` and it silently becomes 2.0, confusing if they're debugging slowness
- Current mitigation: TOML parser catches syntax errors, but not semantic errors
- Recommendations:
  - Validate that rate/workers are numeric before using `.get()`
  - Warn if config file contains unexpected keys (might indicate typo)

**`workers_per_story` config is unused:**
- Issue: Config setting exists but is never read or used anywhere
- Files: `wattpad_crawler/config.py:15`, documented in `_DEFAULT_TOML` but never referenced
- Impact: Users might tweak this setting expecting it to affect parallelism, but it has no effect
- Current mitigation: Not broken, just unused
- Recommendations:
  - Remove from config if not needed soon, OR
  - Implement per-story thread pooling as recommended above and actually use this setting

## Testing & Quality

**Integration tests are skipped and outdated:**
- Issue: VCR cassette not recorded; test file instructs manual recording but cassette doesn't exist
- Files: `tests/integration/test_end_to_end.py:23-25`
- Impact: No automated checks for API breakage; only manual testing against live Wattpad
- Current mitigation: Unit tests cover individual functions
- Recommendations:
  - Record a cassette against a small public story and commit it
  - Run integration test in CI to detect API changes early

**Web routes not tested for malicious inputs:**
- Issue: No tests for path traversal, XSS, or injection attacks in web routes
- Files: `wattpad_crawler/web/routes.py`
- Impact: Security bugs could exist undetected
- Recommendations:
  - Add security-focused tests: test path traversal, XSS in chapter body, SQL injection (SQLite, so low risk but worth testing)

## Known Limitations (Acceptable)

**No HTTPS verification for cover images:**
- Issue: Cover image fetches use httpx with default certificate verification, but no certificate pinning
- Files: `wattpad_crawler/client.py:12-21`
- Impact: MITM attack on cover image fetch is possible (but unlikely given most archives are local-only)
- Recommendation: Acceptable for personal archive tool; note in docs if deploying publicly

**Jinja2 default auto-escaping enabled:**
- Issue: Templates use Jinja2, which auto-escapes variable output by default
- Files: `wattpad_crawler/web/routes.py:57-58`
- Impact: Should prevent most XSS, but raw chapter HTML stored in JSON is not escaped when re-rendered (low risk due to plain-text rendering)
- Recommendation: Continue current approach; document that EPUB/HTML files should only be opened in trusted readers

---

*Concerns audit: 2026-05-03*
