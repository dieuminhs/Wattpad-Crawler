# Pitfalls Research

**Domain:** Python web-scraping pipeline hardening — parallelism, circuit-breakers, sanitization, streamed rendering, integration tests
**Researched:** 2026-05-03
**Confidence:** HIGH (all findings grounded in direct codebase inspection)

---

## Critical Pitfalls

### Pitfall 1: ThreadPoolExecutor Futures Silently Swallow Part Exceptions

**What goes wrong:**
When the `for part in story.parts` loop in `jobs.py:95` is replaced with a `ThreadPoolExecutor.map()` or `as_completed()` dispatch, exceptions raised inside worker threads are stored on the `Future` object and are only re-raised when `future.result()` is called. If the caller iterates `as_completed()` but wraps the whole loop in a single `try/except` instead of calling `.result()` per future, all per-chapter exceptions are swallowed. The manifest never gets `set_part_status(..., "failed")`, the progress event `part.failed` is never emitted, and the job appears to complete normally with silent empties.

**Why it happens:**
The existing sequential code uses a bare `try/except` block inside the `for` loop (lines 106–129 in `jobs.py`). When translated to a thread pool, the natural port wraps `executor.submit(...)` calls in a loop that looks nearly identical — the inner `except` is still present — but the exception escapes the worker and lands on the future, not in the surrounding try/except. The code looks structurally correct but behaves differently.

**How to avoid:**
Call `future.result()` inside a `try/except` for every completed future, not just around the submit loop. Pattern:

```python
futures = {executor.submit(process_part, part): part for part in parts}
for fut in concurrent.futures.as_completed(futures):
    part = futures[fut]
    try:
        fut.result()
    except Exception as e:
        manifest.set_part_status(..., "failed", last_error=str(e))
        emit("part.failed", ...)
```

Also add a unit test that deliberately raises inside the worker and asserts the manifest row ends up as `"failed"`.

**Warning signs:**
- Job reaches `story.done` but multiple parts have no `.txt` / `.json` files on disk
- Manifest `status` column shows `"in_progress"` for parts that the job "processed"
- Progress event stream shows `story.done` without any preceding `part.failed` events despite empty output files
- Worker thread stack trace logged by Python's unhandled-thread-exception hook but not surfaced in job.error

**Phase to address:**
Parallelize chapter fetching (workers_per_story requirement)

---

### Pitfall 2: Thundering Herd on Retry Inside Thread Pool — Token Bucket Exhaustion

**What goes wrong:**
`RateLimitedClient.get()` already has backoff (lines 68–69, 93 in `client.py`), but backoff sleeps inside the worker thread while still holding a taken token. When N=5 worker threads all hit a 429 simultaneously: each called `_bucket.take()` before sending (consuming 5 tokens), each got a 429, each sleeps `time.sleep(wait)` with `wait` = 60 seconds (no Retry-After), then each wakes and calls `_bucket.take()` again in a tight synchronized burst. The bucket is empty and every thread blocks in `take()`, serializing on the lock while hammering it. The burst that caused the 429 is immediately repeated at `rate_per_sec` cadence as soon as the bucket refills — reproducing the original overload.

**Why it happens:**
The token bucket in `TokenBucket.take()` (lines 34–47 in `client.py`) is thread-safe via `_lock`, but it has no notion of "N threads are all waiting for the same endpoint." After the Retry-After sleep, all sleeping threads wake within milliseconds of each other. The bucket replenished at `rate_per_sec` during the 60s sleep, so capacity (currently `max(2, int(rate * 2))`) is fully recharged. All N threads immediately drain it again before any single request has a chance to test whether the ban lifted.

**How to avoid:**
Add per-retry jitter to `_sleep_backoff()` (e.g., `sleep_for * (0.5 + random.random())`) and also apply jitter to the Retry-After sleep in `client.py:75`. For the 429 path specifically, add a module-level threading.Event "cooldown gate" that opens only after the first post-429 request succeeds — all other waiting threads pass through the gate instead of immediately re-submitting. Alternatively, keep `workers_per_story` small enough (2–3) that the burst size never triggers a 429 in the first place.

**Warning signs:**
- After adding threads: 429 responses appear in logs in batches of exactly N (the worker count)
- The total rate-limited throughput *drops* compared to single-threaded mode despite adding workers
- `client.py` log lines show multiple "429 on ... sleeping 60s" messages at the same timestamp

**Phase to address:**
Parallelize chapter fetching (workers_per_story requirement)

---

### Pitfall 3: SQLite `"database is locked"` Under Concurrent Chapter Writes

**What goes wrong:**
Each chapter worker thread calls `manifest.set_part_status(...)` which calls `self.db.execute(...)` followed immediately by `self.db.commit()` (state.py lines 119–128). The `Manifest` object is constructed once in `_build_work()` (routes.py:97) and passed into `archive_story()`. When N threads each call `set_part_status` concurrently on the same connection, SQLite WAL allows only one writer at a time. The second writer will receive `sqlite3.OperationalError: database is locked` if the busy timeout is zero (the default).

WAL pragma is set (state.py:63) but `PRAGMA busy_timeout` is not set anywhere in the codebase. The default busy timeout is 0ms — meaning any attempt to write while another write is in progress raises immediately rather than waiting.

**Why it happens:**
The architecture doc says "WAL allows concurrent readers + one writer." This is true. But the existing code assumes a single sequential writer because the per-chapter loop is sequential today. Adding N thread workers means N concurrent write attempts on the same connection, which WAL cannot serialize automatically — it requires either busy_timeout or connection-per-thread.

**How to avoid:**
One of two mutually exclusive options:

Option A (preferred — simpler): Add `self.conn.execute("PRAGMA busy_timeout = 5000")` in `Manifest.connect()`. This allows SQLite to retry for up to 5 seconds before raising. This is sufficient for a personal tool where write contention is N=2–5 threads.

Option B (more correct): Give each worker thread its own `Manifest` instance (separate `sqlite3.Connection`). Requires passing `output_dir` into the worker and constructing `Manifest(output_dir).connect()` per thread. Eliminates lock contention entirely. Also prevents the thread-safety caveat that sqlite3 connections are not safe to share across threads in check-same-thread mode.

Note: `sqlite3.connect()` defaults to `check_same_thread=True` which raises immediately if a different thread uses the connection. The existing code passes the manifest from the submitting thread into the worker thread — this already violates the default. Add `check_same_thread=False` to `sqlite3.connect()` if sharing, or use Option B.

**Warning signs:**
- `sqlite3.OperationalError: database is locked` in job logs immediately after adding workers
- `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` with the default check_same_thread=True
- Stats show some parts never get status "done" even though their files exist on disk

**Phase to address:**
Parallelize chapter fetching (workers_per_story requirement), specifically the manifest thread-safety sub-problem

---

### Pitfall 4: `body_hash` Dedup Race — Two Threads Write Identical Chapter, Second Write Loses Hash

**What goes wrong:**
The dedup check is:
```python
existing = manifest.get_part(story.story_id, part.part_id)
if existing and existing["status"] == "done":
    emit("part.skipped", ...)
    continue
```
This read-check-then-act pattern is a classic TOCTOU race. In parallel mode, if two workers (e.g., in separate calls to `archive_story` for the same story) both read `"pending"` for the same part, both proceed to fetch, both write files, and both call `set_part_status(..., "done", body_hash=...)`. The second write wins in SQLite (it's a plain UPDATE) but the first writer's `emit("part.done")` already fired. The second file write overwrites the first via `os.replace()` — this is safe but wasteful. The manifest ends up consistent.

The real risk is the **retry idempotency case**: if a re-run starts while the previous run's background thread is still writing part N, `get_part` returns `"in_progress"` (not `"done"`), so the re-run re-fetches the part, potentially writing a different version of the same chapter.

**Why it happens:**
For the within-story parallel case (`workers_per_story` parallelism on distinct parts), each part has a unique `part_id` primary key, so two workers cannot race on the same part — the `story.parts` list is split across workers by the caller. The race only occurs if the same story is submitted twice as two separate jobs simultaneously (which is possible via the web UI). For within-story parallelism with distinct parts, this pitfall does not apply.

**How to avoid:**
Document that the web UI should not allow duplicate story submissions for the same `story_id` while a job is running. Enforce in `_build_work()` or `submit_job()`: check if a running job already has the same story_id and reject the second with a 409. This is simpler than adding transaction-level locking to the manifest.

For the `"in_progress"` case on re-run: the existing code skips only `"done"` parts. Parts in `"in_progress"` will be re-fetched on re-run, which is correct behavior (re-run recovers interrupted parts).

**Warning signs:**
- Two jobs in the web dashboard for the same story_id both show progress simultaneously
- Part files have inconsistent modification times (two timestamps close together)
- `duplicate submission` errors absent — the web UI currently has no guard against this

**Phase to address:**
Parallelize chapter fetching (within-story, distinct parts — no race) + job pruning/dedup guard

---

### Pitfall 5: Progress Event Ordering — SSE Polling Loop Has a Stale-Snapshot Window

**What goes wrong:**
The SSE generator in `routes.py:167` polls `job.snapshot_events(index)` every 250ms. In sequential mode, events arrive one-at-a-time and the snapshot is always consistent. In parallel mode, N workers each call `job.emit(...)` concurrently. `Job.emit()` is thread-safe (protected by `job._lock`). The problem is not data corruption but **event interleaving ordering**: `part.done` for chapter 5 may appear before `part.start` for chapter 3 in the event stream if the worker for chapter 5 ran faster.

The SSE client (`job.html` template) likely renders events in arrival order as a log list. Out-of-order events are cosmetically odd but functionally harmless — ordinal numbers in the event data still identify which part completed. The real risk is any client-side code that assumes `part.start` always precedes `part.done` for the same `part_id` (e.g., a progress bar that counts "in-flight" parts by tracking starts vs. dones).

**Why it happens:**
Thread scheduling is non-deterministic. Worker 3 may complete before worker 1 has even emitted `part.start`. The single-threaded assumption is baked into the sequential design.

**How to avoid:**
Ensure event data always includes `ordinal` (already present) so UI can re-sort or group. Do not build UI logic that requires sequential `part.start` → `part.done` pairing. If a progress bar counts in-flight parts, drive it from a counter of `parts_total - parts_done - parts_failed` rather than tracking start/done pairs.

Also verify the `__status__` sentinel event (the final `"done"/"failed"` signal that terminates the SSE stream, routes.py:179–186) is only emitted after all futures are resolved — it must not fire while workers are still running. This requires joining all futures before `job.set_done()` is called.

**Warning signs:**
- Progress bar shows negative "in-flight" count (done > started)
- `__status__: done` appears before all `part.done` events are delivered (SSE stream ends prematurely)
- Template crashes with KeyError if it assumes event properties from sequential ordering

**Phase to address:**
Parallelize chapter fetching + SSE stream UI

---

### Pitfall 6: `bleach` Silently Drops `<img>` Tags — EPUB Loses Embedded Images

**What goes wrong:**
The EPUB renderer in `epub.py:36` injects raw paragraph HTML (`ch.content = f"<h1>{p['title']}</h1>\n{body}"`) directly into the EPUB item. This `body` is the stored `.html` file, which today contains Wattpad's raw HTML including `<img src="...">` tags. If sanitization is added via `bleach.clean()` with `tags=["p", "em", "strong", "br"]` (a common default allowlist), `<img>` tags are stripped entirely. The EPUB compiles without error, but all images in the story are silently absent.

The `<br>` tag is also at risk if the allowlist omits it. Wattpad uses `<br>` between paragraphs in some chapter layouts. Stripping it collapses two paragraphs into one run-on sentence in the EPUB.

**Why it happens:**
`bleach.clean()` defaults to stripping (not escaping) disallowed tags. `nh3.clean()` behaves similarly. Neither library warns about stripped content — they return a shorter string silently. Developers test sanitization on simple text-heavy chapters and never notice image loss until opening an image-heavy chapter in an EPUB reader.

**How to avoid:**
Build the allowlist explicitly including: `["p", "em", "strong", "b", "i", "br", "img", "span", "a"]` and the required attributes: `{"img": ["src", "alt"], "a": ["href"]}`. For EPUB specifically, image `src` attributes must be relative internal paths (not absolute URLs) after ebooklib processes them — do not sanitize `src` values to relative-only at the sanitization step, as ebooklib handles this separately.

Also test sanitization with the fixture in `tests/fixtures/html_chapters/chapter_with_images.html` and assert that the output contains the same number of `<img>` tags as the input.

**Warning signs:**
- `bleach.clean()` or `nh3.clean()` called with default `tags` list (which does not include `img`)
- EPUB opens in Calibre or Apple Books but image positions are blank
- `.json` part file has `images: [...]` non-empty but the rendered EPUB has no images
- `len(ChapterContent.images) > 0` but EPUB byte count is suspiciously small

**Phase to address:**
HTML sanitization (bleach/nh3 requirement)

---

### Pitfall 7: Sanitization Strips Paragraph `id` Attributes — Comment Anchoring Breaks

**What goes wrong:**
`ChapterContent.paragraphs` stores `[{"id": "abc123", "text": "...", "html": "..."}]` where `"id"` is the `data-p-id` attribute value from Wattpad's HTML. Inline comments reference `paragraph_id` to anchor to a specific paragraph. The future web reader could use these IDs to scroll to the correct paragraph. If sanitization passes through the paragraph HTML but strips all attributes not in an explicit `attrs` allowlist, `data-p-id` is lost from the `"html"` field and the link between comment and paragraph is broken in the stored JSON.

Note: the `"id"` field in the JSON dict is already extracted separately as a Python string (`paragraphs[i]["id"]`), so the comment-anchor relationship in the JSON is not affected by HTML sanitization. The risk is only if someone later rebuilds the paragraph→comment link by re-parsing the stored HTML rather than using the JSON.

However, `bleach` and `nh3` both strip `data-*` attributes by default. If the sanitized HTML is ever used to re-derive paragraph IDs (e.g., in a future reader that parses stored HTML), the IDs will be gone.

**How to avoid:**
Sanitize the `html` field of each paragraph but preserve `data-p-id` in the allowlist: `attrs={"*": ["data-p-id"], "img": ["src", "alt"], "a": ["href"]}`. Or strip the `html` field to plain-text during sanitization and rely exclusively on the `text` and `id` fields in the JSON — this is cleaner but prevents future rich-HTML rendering.

**Warning signs:**
- `data-p-id` absent from sanitized paragraph HTML while present in the raw `.html` file
- Comment objects have non-null `paragraph_id` but no matching element in the EPUB/HTML output

**Phase to address:**
HTML sanitization (bleach/nh3 requirement)

---

### Pitfall 8: EPUB ZIP Corruption from ebooklib In-Memory Assembly on Large Stories

**What goes wrong:**
`render_epub()` in `epub.py` reads all chapter HTML files into memory as Python strings, constructs a full in-memory `EpubBook`, then calls `epub.write_epub(str(out_path), book)`. For a 500-chapter story, all chapter content is in memory simultaneously. `ebooklib.write_epub()` internally assembles a ZIP archive. On Windows, if the process is interrupted (Ctrl-C, crash, OOM) between the start of `write_epub()` and its completion, the partial `.epub` file exists on disk but is not a valid ZIP. Because `write_epub()` does not use the atomic write pattern (`_tmp_path` + `os.replace`), the partial file has the final filename — subsequent runs will not re-render because the file exists, and the reader will get a broken EPUB.

**Why it happens:**
The atomic write helpers (`atomic_write_text`, `atomic_write_bytes`) in `store.py` are used for all file writes except EPUB, which delegates to ebooklib's own writer. ebooklib writes directly to the output path with no staging.

**How to avoid:**
Wrap the `epub.write_epub()` call in the same atomic pattern used elsewhere: write to a temp path, then `os.replace()` to the final path:

```python
import tempfile, os
tmp_out = out_path.with_suffix(".epub.tmp")
epub.write_epub(str(tmp_out), book)
os.replace(tmp_out, out_path)
```

For the memory concern: `ebooklib` does not support streaming. The practical limit for this tool (personal archive, single user) is ~500 chapters before OOM becomes a risk. Defer streaming optimization until a story actually causes OOM; the atomic write fix is a one-line improvement worth doing immediately.

**Warning signs:**
- `.epub` file exists on disk but `zipfile.is_zipfile(path)` returns False
- EPUB opens in one reader (tolerant ZIP parser) but fails in another (strict)
- File size is suspiciously small (e.g., 4KB for a 500-chapter story)
- Ctrl-C during render leaves a partial `.epub` that blocks future re-renders

**Phase to address:**
Streamed rendering requirement (atomic EPUB write is the minimum fix; full streaming deferred)

---

### Pitfall 9: Circuit-Breaker False Positives on Chapter Count-Zero for Short Chapters

**What goes wrong:**
The planned circuit-breaker for content extraction: "if N consecutive chapters return zero `data-p-id` paragraphs (or near-empty text vs. substantial raw HTML), pause loudly." The heuristic `raw_html > 5KB but extracted text < 100 chars` from `CONCERNS.md` will false-positive on:

1. **Interstitial/author-note chapters**: Some Wattpad chapters are legitimately very short — "Author's Note: on hiatus" — which can be < 100 chars extracted text despite substantial raw HTML (navigation chrome, comments section boilerplate).
2. **Paywalled chapters**: Wattpad Premium chapters return a gated HTML page with a login wall. The page HTML can be 30–50KB (full page chrome) but extractable content is intentionally empty. This is not a scraper breakage — it is expected behavior. The circuit-breaker should distinguish "auth failure HTML" from "selector change HTML."
3. **The first chapter of a story**: If the first chapter happens to be short, the circuit-breaker may trip on chapter 1 before any real chapters are archived.

**Why it happens:**
A threshold on absolute character count does not account for content-type variance across chapters. The circuit-breaker is designed to detect selector drift, not content-length variation.

**How to avoid:**
The circuit-breaker condition should be: `data-p-id` elements found = 0 AND raw_html size > threshold AND the raw HTML does NOT contain Wattpad's paywalled/login signals (e.g., class `paid-content`, string `"login to read"`, status code not 200). Also require N consecutive failures (not just 1) before tripping — recommended N=3 from the CONCERNS.md audit. Do not count paywalled chapters as "empty extraction failures."

Additionally, do not start the consecutive-failure counter at chapter 1; reset it when a successful extraction occurs.

**Warning signs:**
- Circuit-breaker trips on chapter 1 of a short story (author-note style)
- Log shows `"circuit breaker tripped"` immediately followed by a successful manual retry
- Stories with premium chapters always trip the circuit breaker

**Phase to address:**
Circuit-breaker on chapter extraction requirement

---

### Pitfall 10: Circuit-Breaker on 4xx/5xx Counts Wattpad Auth Errors as Rate-Limit Events

**What goes wrong:**
The planned circuit-breaker for rate-limit/auth walls needs to distinguish between:
- HTTP 429: rate limiting (wait and retry is correct)
- HTTP 401/403: auth failure (retrying is pointless without a new cookie)
- HTTP 404: story/chapter was deleted (should mark `"gone"`, not trigger circuit-breaker)
- HTTP 503: Wattpad infrastructure overload (transient, retry is appropriate)

If a single consecutive-failure counter increments for all 4xx/5xx codes, a series of 404s from a story with deleted chapters will trip the rate-limit circuit-breaker — stopping the entire archive rather than marking individual chapters as `"gone"`.

**Why it happens:**
The natural implementation is a single `consecutive_errors` counter incremented on any non-2xx response. The distinction between error types requires reading status codes in the circuit-breaker logic, not just counting failures.

**How to avoid:**
Separate counters per error class:
- `consecutive_4xx_auth` (401, 403): triggers auth-failure circuit-breaker after N=2
- `consecutive_429` (429): already handled in client.py with Retry-After; add a story-level cap (e.g., if total 429s per story > 20, abort that story)
- `consecutive_5xx` (500–599): triggers overload pause after N=5
- 404: marks part as `"gone"` in manifest; does not increment any circuit-breaker counter

**Warning signs:**
- Archive stops with "circuit breaker tripped" on a story where several chapters were deleted (404 expected)
- 404s for individual chapters propagate to the story-level circuit-breaker
- Stories with premium chapters (expected 403) abort the entire archive job

**Phase to address:**
Circuit-breaker on rate-limit/auth walls requirement

---

### Pitfall 11: VCR Cassette Breaks Under Threaded Fetches — Request Order Non-Determinism

**What goes wrong:**
VCR (video cassette recorder) libraries (`vcrpy` or `pytest-recording`) match recorded HTTP interactions by request URI and method in the order they were recorded. In single-threaded sequential execution, the cassette replay order is deterministic. Once chapter fetching is parallelized, the order of HTTP requests across worker threads is non-deterministic (thread scheduling). On replay, the cassette matcher receives request N but the cassette has response M at that position — the match fails with "Can't play cassette: no match for request."

The integration test in `tests/integration/test_end_to_end.py` uses VCR cassettes (TESTING.md). Once `workers_per_story` is live, any cassette recorded in sequential mode will break deterministically in parallel mode.

**Why it happens:**
VCR's default `record_mode="none"` (replay-only) uses sequential index-based matching. Parallel request order depends on OS thread scheduling, which varies between machines, Python versions, and load. Even re-recording the cassette will produce a different order on the next run.

**How to avoid:**
Use URI-keyed matching instead of sequential matching. In `vcrpy`, this is `record_mode="none"` with `match_on=["uri", "method"]` — each recorded response is keyed by its URI, not its position. For Wattpad chapter URLs (each chapter has a unique URL), this is safe: `https://www.wattpad.com/123456-slug` is unique per chapter. Cassette recording must use `match_on=["uri", "method"]` at record time and at replay time.

Side effect: if the same URI is requested multiple times (e.g., on retry), URI-keyed matching will return the same recorded response for all retries. For the integration test this is acceptable — retries are not the test target.

**Warning signs:**
- Integration test passes when `workers_per_story=1` but fails with `workers_per_story=3`
- Error message: `"Can't play cassette entry"` or `"request not found in cassette"` from vcrpy
- Test is non-deterministic: passes on some runs, fails on others

**Phase to address:**
Integration test / VCR cassette requirement (design the cassette structure for parallel mode from the start)

---

### Pitfall 12: Job Pruning Races With Active SSE Stream — Client Gets 404 Mid-Stream

**What goes wrong:**
When job history pruning is implemented (cap `JobManager` to last N jobs), a prune operation removes job objects from `_jobs` and `_order`. If a browser SSE client is mid-stream on `GET /jobs/{job_id}/stream`, the `event_gen()` coroutine holds a reference to the `job` object (captured in the closure at `routes.py:163`). Pruning removes the job from `JobManager._jobs` but the `job` object itself is not garbage collected because `event_gen` holds a reference. This is safe for the in-flight stream — it continues working.

However, if the client disconnects and reconnects (e.g., browser refresh), `GET /jobs/{job_id}/stream?after=50` calls `mgr.get(job_id)` which now returns `None` (job was pruned) → 404. The user's browser reconnects mid-archive and gets "job not found," appearing as if the job crashed.

Also: pruning must never remove a job that is in `JobRunner._running`. The current `JobRunner._running` set tracks running job IDs. A prune policy must check this set before evicting.

**Why it happens:**
Pruning is designed to be simple (evict oldest N jobs). The interaction between ephemeral job objects, SSE stream reconnection, and the running-job guard is easy to miss because the failure only manifests on browser refresh during a long-running job.

**How to avoid:**
Pruning policy: only prune jobs where `job.status` is `"done"` or `"failed"` (completed jobs). Never prune running jobs. For SSE reconnect: the `after` parameter already enables resumable streams. If `mgr.get(job_id)` returns `None` for a running job, it means the job was pruned while running — this should be treated as a bug, not a 404. Add an assertion or log.ERROR.

Implementation: `JobManager.prune(keep_n: int)` should only evict from the tail of `_order` where `_jobs[jid].status in ("done", "failed")`.

**Warning signs:**
- SSE stream on dashboard stops updating and browser console shows `EventSource` error on reconnect
- `/jobs/{id}/stream` returns 404 while the job thread is still writing files to disk
- Dashboard shows blank job list immediately after submitting a job (prune too aggressive)

**Phase to address:**
Job history pruning requirement

---

### Pitfall 13: Comment Recursion Cap Silently Truncates Data Without User Notice

**What goes wrong:**
`api/comments.py:9` sets `_MAX_PAGES = 200` which caps at 20,000 comments per chapter. The code in `_fetch_all()` exits the while loop silently when `pages >= _MAX_PAGES` — no warning is logged, no event emitted, no field in the stored JSON indicates truncation occurred. Users archiving popular stories believe their archive is complete when it is not.

The reply recursion in `_parse_one()` (lines 20–25) recurses on `replies_raw` without depth limit. While `_MAX_PAGES` bounds the pagination, a single comment object with deeply nested replies (e.g., from a malformed/adversarial API response) is not bounded. On CPython with default stack depth (~1000 frames), a chain of 1000 replies would stack-overflow.

**Why it happens:**
`_MAX_PAGES` was documented in code as "200 pages (20k comments per chapter)" but the silent-truncation behavior was not flagged as a user-visible issue. The recursive `_parse_one` is a natural tree-traversal pattern that works for normal reply depths (2–3 levels on Wattpad) but has no guard against pathological inputs.

**How to avoid:**
1. Log a warning when `pages` hits `_MAX_PAGES`: `logger.warning("comment pagination capped at %d pages for part %s", _MAX_PAGES, part_id)` and emit a progress event `"comments.truncated"` with count.
2. Add `max_depth` parameter to `_parse_one(raw, depth=0, max_depth=10)`. When `depth >= max_depth`, record the comment but return it with `replies=[]` and log a warning.
3. Store a `"truncated": true` flag in the comments JSON when either cap is hit — this makes the data-loss visible to future inspection tools.

**Warning signs:**
- Archive of a very popular story (10k+ comments/chapter) produces comment JSON without any warning in logs
- `_fetch_all` loop exits without `break` statement being hit — only detectable by adding instrumentation
- Python `RecursionError` in job log from `_parse_one` (only on pathological inputs)

**Phase to address:**
Bounded comment-reply recursion requirement

---

### Pitfall 14: Pre-existing Manifest Schema — `check_same_thread` Default Breaks on Share

**What goes wrong:**
`Manifest.connect()` calls `sqlite3.connect(self.path)` without `check_same_thread=False` (state.py:58). Python's sqlite3 module defaults to `check_same_thread=True`, meaning the connection will raise `ProgrammingError: SQLite objects created in a thread can only be used in that same thread` if any method is called from a different thread than the one that called `connect()`.

In the current single-threaded CLI, this is not triggered. In the web `_build_work()` (routes.py:97), the `Manifest` is constructed and connected inside the worker thread — so the creating thread and using thread are the same. But if a future refactor (or even a test helper) constructs the Manifest on the main thread and passes it to a worker, it will fail with a confusing error.

For the parallelism phase: if the chosen approach is to share a single `Manifest` instance across chapter worker threads (the simpler implementation path), this error will fire on the second thread that calls any manifest method.

**Why it happens:**
The `check_same_thread=True` default is a safety rail, not a bug. It protects against accidental cross-thread use. The connection is designed to be created and used in the same thread. The current code happens to satisfy this, but the hardening work will likely create pressure to share the connection.

**How to avoid:**
Do not share a single `Manifest` connection across worker threads. Either:
- Create one `Manifest` per worker thread (instantiate inside the thread function)
- Or add `check_same_thread=False` to `sqlite3.connect()` AND add `busy_timeout` AND document the threading contract

Per-thread Manifest instances are the cleaner choice: no shared mutable state, no lock contention beyond SQLite WAL, and no change to the schema or existing API.

**Warning signs:**
- `ProgrammingError: SQLite objects created in a thread` in logs immediately on first parallelism attempt
- Manifest methods work in test but fail under JobRunner because test creates Manifest in main thread

**Phase to address:**
Parallelize chapter fetching (manifest thread-safety sub-problem) — must be addressed before any parallel chapter write is attempted

---

### Pitfall 15: Cookie Validation Endpoint Returns 200 on Expired Session

**What goes wrong:**
The planned cookie validation: "validate cookie on save with a quick test API call." The risk is choosing a Wattpad API endpoint that returns HTTP 200 for both authenticated and unauthenticated requests, making it useless as an auth probe. Some Wattpad public API endpoints (e.g., public story metadata, public user profile) return 200 regardless of cookie validity because the content is public. Validating against such an endpoint confirms "the network works and Wattpad is up" but not "the cookie is valid."

Additionally, Wattpad session cookies may be valid at save time but rotate 5–15 minutes later (session refresh pattern). A cookie validated on `/setup` POST may be expired by the time the first archive job starts.

**Why it happens:**
Developers grab the first endpoint that returns a sensible JSON response and use it as the auth probe. Checking the HTTP status code alone is insufficient if the endpoint doesn't require auth.

**How to avoid:**
Use a Wattpad endpoint that requires authentication and returns 401/403 without a valid cookie. Candidate: `GET /api/v3/users/{username}/library` (the user's private reading library) — this requires auth. A simpler option: `GET /api/v3/users/me` which returns the authenticated user's profile (includes `id`, `name`) and returns 401 or a `"Not authenticated"` error body without a cookie.

Check both: HTTP status code AND that the response body contains an `"id"` field (not an error object). Store the validated username in the config to show in the UI ("Validated as: @authorname").

Also: after validation, show the expiry warning in the web UI ("Cookie validated. Wattpad sessions typically last N hours — re-validate if you get auth errors during archiving").

**Warning signs:**
- Cookie validation passes for a completely blank cookie (`cookie = ""`)
- Validation passes but first chapter fetch returns 401
- The test endpoint is a public story metadata URL (not user-scoped)

**Phase to address:**
Cookie validation requirement

---

### Pitfall 16: `_save_cookie()` Is Not Atomic — Config Corruption on Race

**What goes wrong:**
`_save_cookie()` in `routes.py:22` reads the entire `_config.toml` as text, modifies it in memory, then calls `config_path.write_text(...)` directly (line 39) — a non-atomic write. If the web server process crashes or the system interrupts between the `write_text()` opening the file for writing (truncating it) and completing the write, `_config.toml` is left with 0 bytes or partial content. On next startup, `load_config()` raises `ConfigError` (TOML parse failure) and the entire web app fails to start.

Additionally, there is no lock around the read-modify-write cycle. If two browser tabs simultaneously submit the `/setup` POST (unlikely but possible), both read the old config, both compute a new config with their respective cookie values, and both race to `write_text()` — the last write wins and the other's cookie is silently lost.

**Why it happens:**
The atomic write pattern from `store.py` (`_tmp_path` + `os.replace`) is used for all archive files but was not applied to the config file, which is less frequently written and not in the write-critical path.

**How to avoid:**
Apply the same atomic write pattern: write new content to `_config.toml.tmp`, then `os.replace(_config.toml.tmp, _config.toml)`. This ensures the config is either fully written or unchanged, never partially written.

**Warning signs:**
- `_config.toml` is 0 bytes after a browser crash during `/setup` POST
- Web app fails to start with `ConfigError: TOML parse failure` after a seemingly successful cookie save
- `write_text` used directly instead of via `atomic_write_text` from `store.py`

**Phase to address:**
Cookie validation requirement (config write is modified in the same phase)

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Sharing `Manifest` connection across threads | Simpler code, no refactor needed | `ProgrammingError` on first parallel write; SQLite lock errors | Never — use per-thread Manifest or `check_same_thread=False` + busy_timeout |
| Single consecutive-failure counter for all HTTP errors | Simple circuit-breaker implementation | 404s from deleted chapters trip rate-limit circuit-breaker | Never — separate counters by error class |
| `bleach.clean()` with default `tags` list | Quick to add sanitization | Silently removes `<img>` and `data-p-id` from EPUB/HTML output | Never — always build explicit allowlist |
| Calling `epub.write_epub()` directly to final path | No extra temp file | Interrupt-safe problem: partial `.epub` blocks future re-renders | Never for output files — wrap in atomic pattern |
| URI-based VCR matching added after recording cassettes | Cassette already recorded | Sequential cassettes break under parallel fetches | Never — design cassette matching before recording |
| Pruning based on age alone (no running-job check) | Simple TTL-based pruning | Prunes running jobs whose SSE stream is active | Never — always check `job.status` before pruning |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Wattpad cookie auth | Validate against a public endpoint that returns 200 for anyone | Validate against `/api/v3/users/me` or library endpoint that returns 401 without valid auth |
| Wattpad Premium chapters | Treat 403 on chapter fetch as network error → circuit-breaker trip | Treat 403 per-chapter as `"private"` status in manifest; do not count toward circuit-breaker |
| ebooklib write_epub | Pass final output path directly | Write to temp path, then `os.replace()` to final |
| sqlite3 cross-thread | Pass Manifest object from main thread into worker threads | Construct and connect Manifest inside each worker thread |
| VCR cassette replay | Default sequential matching | Use `match_on=["uri", "method"]` for parallel-safe replay |
| bleach/nh3 sanitization | Use default tags allowlist | Build explicit allowlist including `img`, `br`, `span`, `data-p-id` attribute |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| All chapter workers wake after Retry-After simultaneously | 429 responses in batches of N exactly matching worker count | Jitter on Retry-After sleep (`wait * (0.5 + random.random())`) | Immediately on first 429 with N>1 workers |
| `Job.events` grows unbounded per job | Long archive jobs consume 100s of MB for event lists alone | Cap events at 1000; emit `"events.truncated"` when cap hit | Stories with 500+ chapters (~1500 events per story) |
| EPUB full in-memory assembly for large stories | OOM kill during render on stories > 300 chapters | Atomic temp-file write is the immediate fix; defer true streaming until measured | ~300-500 chapters depending on chapter size |
| `JobManager._jobs` never pruned | Web server memory grows unboundedly on long sessions | Prune completed jobs after keep_n reached | After ~100 library jobs in one session |
| SQLite write contention without busy_timeout | `OperationalError: database is locked` on first parallel chapter write | Set `PRAGMA busy_timeout = 5000` in `Manifest.connect()` | Immediately with any N>1 parallel writers sharing a connection |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing raw Wattpad HTML verbatim in `.html` and EPUB | XSS if EPUB/HTML opened in browser-based reader that executes scripts | Sanitize paragraph HTML before storage with allowlist; do not use default tags |
| `_save_cookie()` non-atomic write | Config corruption on crash → web app fails to start | Use atomic write (temp + os.replace) for all config writes |
| Blank/default cookie passing validation | Silent auth failures on all private-story fetches | Validate cookie against auth-required endpoint; reject blank/placeholder cookies |

---

## "Looks Done But Isn't" Checklist

- [ ] **Sanitization:** `bleach.clean()` or `nh3.clean()` added — verify `<img>`, `<br>`, and `data-p-id` survive the sanitizer with a fixture containing all three
- [ ] **Parallel workers:** `workers_per_story` parameter passed to `ThreadPoolExecutor(max_workers=...)` — verify `workers_per_story=1` produces identical output to the current sequential mode (regression test)
- [ ] **Circuit-breaker:** N consecutive empty-extraction chapters triggers loud error — verify the counter resets after a successful extraction (doesn't trip on non-consecutive failures)
- [ ] **Job pruning:** `JobManager.prune()` implemented — verify a running job is never pruned (add assertion or test with a long-running mock job)
- [ ] **VCR cassette:** Cassette recorded and committed — verify it replays correctly with `workers_per_story > 1` (not just with sequential fetches)
- [ ] **Cookie validation:** Auth probe returns 401 for expired/blank cookie — verify by testing with a deliberately invalid token value
- [ ] **EPUB atomic write:** `epub.write_epub()` writes to temp path — verify a simulated interrupt (raise after write_epub but before os.replace) leaves no partial `.epub` at the final path
- [ ] **Comment recursion cap:** Depth limit applied in `_parse_one` — verify a fixture with 15-level deep replies truncates at the configured depth with a warning logged

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Futures swallow exceptions, parts silently empty | MEDIUM | Re-run archive for affected story (idempotent); check manifest for `in_progress` rows; fix the future.result() call pattern |
| Thundering herd 429 ban | MEDIUM | Wait 1-24 hours for IP cooldown; lower `rate_limit_per_sec` in config; re-run with `workers_per_story=1` |
| SQLite `database is locked` errors | LOW | Add `PRAGMA busy_timeout = 5000`; re-run archive; manifest rows will be corrected on re-run |
| EPUB partial write (broken zip) | LOW | Delete the partial `.epub`; re-run render phase; fix `write_epub` to use atomic pattern |
| Config corrupted (0-byte TOML) | LOW | Manually recreate `_config.toml` with cookie and defaults; web app restarts normally |
| VCR cassette breaks under parallel fetches | LOW | Re-record cassette with `match_on=["uri", "method"]`; parallelism does not affect cassette content, only matching strategy |
| Comment truncation silently loses data | HIGH (data is gone) | Cannot recover — the Wattpad API data was not stored; only mitigation is increasing `_MAX_PAGES` or removing cap for future runs |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Futures silently swallow exceptions | Parallelize chapter fetching | Unit test: worker raises exception → manifest row = "failed" |
| Thundering herd on 429 | Parallelize chapter fetching | Integration test: N=3 workers with cassette returning 429 → jittered retry, not synchronized burst |
| SQLite `database is locked` / `check_same_thread` | Parallelize chapter fetching | Pytest: N=3 threads call `set_part_status` concurrently on same output_dir, no OperationalError |
| `body_hash` dedup TOCTOU | Parallelize chapter fetching + job submit guard | Web UI test: submit same story_id twice → second submission rejected |
| Progress event ordering | Parallelize chapter fetching + SSE | SSE test: events include `ordinal` field; no `part.done` before `story.start` |
| `bleach` strips `<img>` and `data-p-id` | Sanitization phase | Unit test: `sanitize(chapter_with_images_fixture)` → same img count in, same img count out |
| `bleach` strips `data-p-id` attribute | Sanitization phase | Unit test: `sanitize(paragraph_html)` → `data-p-id` attribute preserved |
| EPUB partial write on interrupt | Streamed rendering phase | Test: simulate crash mid-render → no partial `.epub` at final path |
| Circuit-breaker false positive on short/paywalled chapters | Circuit-breaker phase | Unit test: 3 consecutive "Author's Note" chapters (< 100 chars, small HTML) do not trip breaker |
| Circuit-breaker 404 trips rate-limit breaker | Circuit-breaker phase | Unit test: 5 consecutive 404s → parts marked "gone", no circuit-breaker event |
| VCR cassette breaks under parallel fetches | Integration test phase | Integration test: `workers_per_story=3` with cassette → all chapters archived correctly |
| Job pruning races with SSE stream | Job pruning phase | Test: prune while job is running → job not removed from registry |
| Comment recursion cap silent | Bounded recursion phase | Unit test: reply fixture at depth 15 → depth capped at 10, warning logged, top-level comment preserved |
| `check_same_thread` on shared Manifest | Parallelize chapter fetching | Pytest: Manifest created in main thread, used in worker thread → ProgrammingError expected unless using per-thread instances |
| Cookie validation endpoint returns 200 for unauthenticated | Cookie validation phase | Integration test: blank cookie → validation endpoint returns non-200 or error body |
| `_save_cookie()` non-atomic write | Cookie validation phase | Test: simulate interrupt during write → `_config.toml` not truncated |

---

## Sources

- Direct codebase inspection: `local_story_archive/client.py`, `local_story_archive/archive/state.py`, `local_story_archive/jobs.py`, `local_story_archive/web/runner.py`, `local_story_archive/web/routes.py`, `local_story_archive/api/comments.py`, `local_story_archive/archive/store.py`, `local_story_archive/render/epub.py`, `local_story_archive/render/html.py`, `local_story_archive/scrape/chapter_html.py`
- `.planning/codebase/CONCERNS.md` — codebase audit (2026-05-03)
- `.planning/codebase/ARCHITECTURE.md` — architecture analysis (2026-05-03)
- `.planning/codebase/TESTING.md` — test patterns (2026-05-03)
- Python stdlib `sqlite3` docs: `check_same_thread` default behavior, `busy_timeout` pragma
- `concurrent.futures` docs: exception re-raise behavior on `future.result()`
- `vcrpy` docs: `match_on` parameter and cassette replay ordering
- `bleach`/`nh3` docs: default tags allowlist behavior (strips vs. escapes)

---
*Pitfalls research for: Python Wattpad-archiver hardening milestone*
*Researched: 2026-05-03*
