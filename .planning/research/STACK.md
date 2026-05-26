# Stack Research

**Domain:** Python scraper hardening — parallelism, sanitization, circuit-breaking, testing, streaming EPUB
**Researched:** 2026-05-03
**Confidence:** HIGH (all versions verified against PyPI; architecture claims verified against source)

---

## Recommended Stack

### Core Technologies (additions to existing stack)

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| nh3 | 0.3.5 | HTML sanitization of paragraph HTML before storage | Rust-backed, ~20x faster than bleach, bleach is deprecated, actively maintained, full allowlist API |
| vcrpy | 8.1.1 (already installed) | VCR cassette recording + replay for integration tests | Already in pyproject.toml; v8.0.0 rewrote httpx support to patch httpcore — now works reliably |
| pytest-recording | 0.13.4 | pytest plugin wrapper over vcrpy with `--record-mode` flag | Better DX than bare pytest-vcr for httpx; `--record-mode=once` enables the one-time cassette workflow the existing test already documents |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| concurrent.futures (stdlib) | Python 3.11+ | ThreadPoolExecutor for parallel chapter fetches | Already available; no new dep needed; shared TokenBucket provides rate-limiting across workers |
| threading (stdlib) | Python 3.11+ | threading.Lock already used in TokenBucket | Circuit-breaker state and job-history pruning counters need the same lock pattern |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| ruff 0.5+ | Linting/formatting | Already in stack; no change needed |
| pytest 8.0+ | Test runner | Already in stack; no change needed |

---

## Detailed Decisions

### 1. Parallelism: ThreadPoolExecutor over async httpx

**Decision: `concurrent.futures.ThreadPoolExecutor` with the existing `RateLimitedClient` and `TokenBucket`.**

The existing stack is synchronous (`httpx.Client`, not `httpx.AsyncClient`). The `RateLimitedClient.get()` method is a blocking call. The `TokenBucket.take()` call already uses `threading.Lock` and `time.sleep()` — it is inherently thread-safe and works correctly when called from multiple threads simultaneously.

The pattern for `workers_per_story` is:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _fetch_part(client, deps, part):
    raw_html = deps.fetch_chapter_html(client, part.url)   # blocks on token bucket
    content = deps.parse_chapter(raw_html)
    inline = deps.fetch_inline_comments(client, part.part_id)
    end = deps.fetch_end_comments(client, part.part_id)
    return part, content, raw_html, inline, end

with ThreadPoolExecutor(max_workers=cfg.workers_per_story) as pool:
    futures = {pool.submit(_fetch_part, client, deps, part): part for part in pending_parts}
    for fut in as_completed(futures):
        part, content, raw_html, inline, end = fut.result()
        # store + manifest updates must be serialized back on the calling thread
        store.write_part_files(...)
        manifest.set_part_status(...)
        emit(...)
```

Key constraint: `store.write_part_files` and `manifest.set_part_status` must run on a single serialized thread (the `archive_story` caller). SQLite WAL handles concurrent *readers* but the archive pipeline is a single writer. Fetching is parallelized; writes are not. This is the correct split.

**Why NOT async httpx:**
- Switching `RateLimitedClient` to async would require converting `client.py`, `jobs.py`, all API modules, and the `JobRunner` thread-dispatch model — a full rewrite of the pipeline, not a hardening change.
- FastAPI routes already use sync background threads via `JobRunner`; mixing async into those threads requires an event loop per thread or `asyncio.run()` inside each thread, which is fragile.
- The existing `TokenBucket` is already thread-safe. A `threading.Semaphore` provides the concurrency cap (`max_workers`), while the `TokenBucket` provides the rate cap. The combination is correct and simple.
- Async would require `asyncio.Semaphore`, an event loop, and converting every `await` call — all for an I/O-bound problem that `ThreadPoolExecutor` handles cleanly at this scale (single user, 3–5 workers).

**Confidence: HIGH** — This matches the PROJECT.md constraint: "Stay single-process. In-story parallelism via `concurrent.futures.ThreadPoolExecutor`; rate limit shared via the existing `RateLimitedClient` token bucket."

---

### 2. HTML Sanitization: nh3 over bleach

**Decision: `nh3 == 0.3.5`.**

- **bleach 6.3.0** was officially deprecated January 2023. It continues receiving Python version compatibility updates (e.g., 3.14 support in 6.3.0, Oct 2025) but the maintainer declared it end-of-life because its underlying parser `html5lib` is unmaintained. Using a deprecated library with an unmaintained parser in an EPUB pipeline is unnecessary technical debt.
- **nh3 0.3.5** (released April 25, 2026) is Python bindings to the Rust `ammonia` library, which is actively maintained by the Rust security community. It is approximately 20x faster than bleach on equivalent inputs. Its allowlist API is a superset of bleach's.

**API surface for this use case:**

```python
import nh3

SAFE_TAGS = {"p", "br", "b", "i", "em", "strong", "span", "a", "img"}
SAFE_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt"},
    "span": {"class"},
}

def sanitize_para_html(raw: str) -> str:
    return nh3.clean(
        raw,
        tags=SAFE_TAGS,
        attributes=SAFE_ATTRS,
        url_schemes={"http", "https"},
        strip_comments=True,
        link_rel="noopener noreferrer",
    )
```

**EPUB compatibility:** nh3 outputs clean XHTML-safe HTML. `ebooklib` wraps chapter content in its own XHTML template; the sanitized fragment is inserted as innerHTML. The output is structurally no different from what bleach would produce — both strip disallowed tags and attributes. There is no EPUB-specific incompatibility.

**Do not use `html.escape()`** for this task. `html.escape()` escapes all HTML entities, turning `<b>bold</b>` into `&lt;b&gt;bold&lt;/b&gt;` — destroying all formatting. It is appropriate only for inserting untrusted *text* into an HTML context, not for sanitizing an HTML *fragment*.

**Confidence: HIGH** — Version verified on PyPI (0.3.5, April 2026). Deprecation of bleach verified on PyPI.

---

### 3. Circuit Breakers: Roll a 30-line in-process implementation

**Decision: No external library. Implement a `FailureCounter` class inline.**

The three external options are:
- **pybreaker 1.4.1** (Sep 2025): Full-featured, Redis-backed state, listener hooks. Designed for distributed microservices. API is decorator-based, which does not fit the "detect N consecutive failures in a loop and raise loud" pattern needed here.
- **circuitbreaker 2.1.3**: Decorator-based. Same mismatch.
- **purgatory**: asyncio-focused. Irrelevant for a sync stack.

All three libraries are designed for wrapping individual *function calls* (retry individual HTTP requests) not for detecting *patterns across a sequence* (e.g., "5 consecutive chapters had empty extraction"). The hardening requirements are:

1. **Extraction circuit-breaker**: track consecutive chapters returning zero `data-p-id` paragraphs vs. substantial raw HTML.
2. **Rate/auth wall detector**: track consecutive 4xx/5xx responses, detect IP-throttle patterns.

Neither maps to "call this function, if it raises, increment a counter" — they need custom comparison logic (raw HTML length vs. extracted text length, specific HTTP status codes vs. network errors, etc.).

A 30-line implementation:

```python
class ConsecutiveFailureBreaker:
    """Raises after threshold consecutive failures; resets on success."""
    def __init__(self, threshold: int, label: str):
        self._threshold = threshold
        self._count = 0
        self._label = label

    def record_success(self) -> None:
        self._count = 0

    def record_failure(self) -> None:
        self._count += 1
        if self._count >= self._threshold:
            raise CircuitOpenError(
                f"{self._label}: {self._count} consecutive failures — stopping."
            )

    def failures(self) -> int:
        return self._count
```

This is 25 lines, directly testable, has no external dependencies, no decorator magic, and slots cleanly into the existing `archive_story` loop alongside `emit()` calls.

**Confidence: HIGH** — The complexity of the problem matches the complexity of the solution. External libraries add config, docs debt, and a dependency for no gain at this scale.

---

### 4. VCR / Integration Testing: vcrpy 8.1.1 + pytest-recording 0.13.4

**Decision: Replace `pytest-vcr` with `pytest-recording` (same underlying vcrpy, better DX).**

Current state: `pyproject.toml` has `pytest-vcr>=1.0.2` and `vcrpy>=6.0`. The integration test already uses `@pytest.mark.vcr` — a pytest-vcr marker. This works, but `pytest-recording` is a strict superset that adds `--record-mode` CLI flag, `--block-network` flag, and better cassette management, all while being powered by the same vcrpy.

The two plugins are **incompatible** — you must remove `pytest-vcr` and add `pytest-recording`. The marker `@pytest.mark.vcr` works the same in both.

**httpx compatibility in vcrpy 8.x:**
- vcrpy 8.0.0 (Jan 2026) **rewrote httpx support** to patch `httpcore` instead of `httpx` directly. This resolved longstanding `httpx.ResponseNotRead` exceptions and `KeyError: 'follow_redirects'` errors. vcrpy 8.1.1 (current) is the stable release.
- vcrpy officially supports: `aiohttp, boto3, http.client, httplib2, requests, tornado.httpclient, urllib2, urllib3, httpx, httpcore`.
- The existing cassette stub in `tests/integration/` uses `@pytest.mark.vcr(cassette_library_dir=...)` — this is compatible with both plugins.

**Why not respx 0.23.1:**
`respx` is a *mock* library, not a *record-and-replay* library. It requires you to manually write expected request/response pairs. For an integration test against Wattpad's full archive pipeline, VCR record-then-replay is correct: record against a real story once, commit the cassette, replay forever. `respx` would require manually crafting every response — defeating the purpose of catching real API breakage.

**Why not pytest-httpx 0.36.2:**
Same objection: it is a fixture-based mock, not a cassette system. Suitable for unit tests where you control the exact response; not suitable for the "record real Wattpad responses and replay them" integration test workflow.

**Migration change to pyproject.toml:**

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-recording>=0.13.4",   # replaces pytest-vcr
  "vcrpy>=8.1.1",               # pin to 8.x for stable httpx support
  "ruff>=0.5",
]
```

Remove `pytest-vcr` entirely. The `@pytest.mark.vcr` decorator in the test file works unchanged.

**Confidence: HIGH** — vcrpy version verified on PyPI (8.1.1 installed). pytest-recording version verified (0.13.4). Incompatibility between pytest-vcr and pytest-recording is documented on PyPI.

---

### 5. Streamed Rendering: No streaming API in ebooklib; use intermediate-file pattern

**Decision: For TXT and HTML renderers, switch to streaming file writes. For EPUB, use the intermediate-file pattern (write chapters to disk first, then call `epub.write_epub()`).**

**ebooklib 0.20 architecture:**
`EpubWriter` is a build-then-write design. All `EpubItem` objects are added to the `EpubBook` in memory, then `epub.write_epub()` serializes the entire book to a ZIP file in one pass. There is no incremental/streaming API. The EPUB spec requires a `content.opf` manifest listing all items before the items are written, which structurally prevents a true streaming approach at the EPUB level.

**TXT and HTML renderers** (in `render/txt.py` and `render/html.py`) currently accumulate all chapter strings in a list and join them. Switch to:

```python
with open(out_path, "w", encoding="utf-8") as f:
    f.write(header)
    for part_path in sorted(parts_dir.glob("*.txt")):
        f.write(part_path.read_text(encoding="utf-8"))
        f.write("\n\n")
```

This is O(1) memory relative to the story size. Chapter files are already written to disk by `store.write_part_files()`.

**EPUB renderer:**
The current approach reads all chapter TXT files, builds `EpubHtmlFile` objects in memory, and calls `write_epub()`. This is already close to O(n-chapters) memory, not O(1). The real OOM risk is the accumulated `EpubHtmlFile` list for a 500-chapter story.

The correct mitigation is: let `write_epub()` do its work (it already writes chapters as individual files inside the ZIP), but avoid pre-reading all chapter content into a Python list. Build each `EpubHtmlFile` from the already-stored chapter HTML file one at a time, `book.add_item()` it, then call `write_epub()`. This reduces the peak footprint to one chapter's worth of HTML in memory at a time during construction.

Do not pursue the `zipfile` streaming pattern (e.g., `zipstream-new`). EPUB requires a specific ZIP structure (uncompressed `mimetype` first, then directories in a specific order, then `content.opf`) that custom ZIP streaming would need to replicate. This is fragile and would effectively mean reimplementing ebooklib. The benefit for a personal archive tool does not justify the complexity.

**Confidence: MEDIUM** — ebooklib 0.20 source confirmed build-then-write (no streaming API). Memory analysis is based on reading the existing renderer code. OOM threshold estimate is heuristic.

---

### 6. Cookie Validation: Use `/api/v3/users/{username}/library` with a limit=1 guard

**Decision: No dedicated auth-check endpoint is needed. Use the existing library fetch endpoint with a minimal request.**

Wattpad's unofficial API v3 does not have a documented `/users/me` or `/auth/validate` endpoint. The official developer API was discontinued. What exists:

- `/api/v3/stories/{id}` — returns 200 for public stories even without a cookie. Useless for cookie validation.
- `/api/v3/users/{username}/library` — returns the user's library. Requires a valid session cookie. Returns 401 or a redirect to login for expired/missing cookies.
- `/api/v3/users/{username}/lists` — same auth requirement.

**Recommended validation call:**

```python
def validate_cookie(client: RateLimitedClient, username: str) -> bool:
    """Returns True if cookie is valid. Raises on network error."""
    url = f"https://www.wattpad.com/api/v3/users/{username}/library?limit=1"
    try:
        resp = client._client.get(url)  # bypass rate limiter for a single probe
        return resp.status_code == 200
    except httpx.RequestError:
        raise
```

This call is cheap (limit=1 returns minimal JSON), exercises the auth path, and uses an endpoint the code already calls in production (`api/user.py:LIBRARY_URL`). Status 401 or 403 means cookie expired or invalid. Status 200 means auth is live.

**Mid-job auth-failure detection:** Add 401/403 recognition in `RateLimitedClient.get()`:

```python
if resp.status_code in (401, 403):
    raise AuthFailedError(f"Auth failure on {url}: HTTP {resp.status_code}")
```

`AuthFailedError` should be a custom exception that the `archive_story` loop catches and surfaces as a loud `part.failed` event with a clear message, stopping further fetches.

**Why not a public story endpoint:** A 200 from `/api/v3/stories/{id}` does not confirm cookie validity — public stories return 200 with no auth. The library endpoint is the only endpoint in the existing stack that requires auth.

**Confidence: MEDIUM** — Based on reverse-engineered API behavior. Wattpad could change endpoint behavior without notice. The approach is idiomatic and matches patterns in community Wattpad wrappers, but no official documentation exists to verify the exact 401 behavior.

---

### 7. Extraction Integrity: Ratio heuristic, no external library

**Decision: Implement a size-ratio check in `extract_chapter()` or the calling loop. No external library needed.**

The concern from `CONCERNS.md`: if `extract_chapter()` returns empty `text` but `raw_html` is substantial, the tool silently archives an empty chapter.

The check:

```python
MIN_HTML_SIZE_FOR_SUSPECT = 5_000    # bytes: below this, an empty story is plausible
MIN_TEXT_RATIO = 0.01                # text must be at least 1% of raw HTML size

def check_extraction_integrity(raw_html: str, content: ChapterContent) -> None:
    """Raise ExtractionFailedError if content looks like a soft failure."""
    if len(raw_html) < MIN_HTML_SIZE_FOR_SUSPECT:
        return  # short page — empty is plausible
    text_len = len(content.text.strip())
    html_len = len(raw_html)
    if text_len == 0 and not content.paragraphs:
        raise ExtractionFailedError(
            f"Extracted zero content from {html_len}-byte HTML — "
            "likely selector change or soft-failure page. "
            "Check raw HTML to diagnose."
        )
    if text_len / html_len < MIN_TEXT_RATIO:
        raise ExtractionFailedError(
            f"Extraction ratio too low ({text_len}/{html_len} = "
            f"{text_len/html_len:.3f}) — possible soft-failure page."
        )
```

This is more reliable than any external library. Libraries like Trafilatura or Readability detect *article content* from arbitrary web pages. This codebase already knows exactly what it expects: `data-p-id` paragraphs inside `.page-container`. The check is validating extraction output quality, not re-running a new extraction.

The `CONCERNS.md` heuristic is correct: "if raw_html > 5KB but extracted text < 100 chars, flag as likely extraction failure." The ratio approach is slightly more robust than a fixed character count threshold.

**Confidence: HIGH** — This is a domain-specific check; no library could know Wattpad's extraction contract better than the code that implements it.

---

## Installation

```toml
# Add to [project.dependencies]
"nh3>=0.3.5"

# Replace in [project.optional-dependencies].dev:
# Remove: "pytest-vcr>=1.0.2"
# Change: "vcrpy>=6.0" → "vcrpy>=8.1.1"
# Add:    "pytest-recording>=0.13.4"
```

```bash
pip install "nh3>=0.3.5"
pip install "pytest-recording>=0.13.4" "vcrpy>=8.1.1"
pip uninstall pytest-vcr
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| nh3 0.3.5 | bleach 6.3.0 | Never for new code — bleach is deprecated; only if you have an existing bleach allowlist config you cannot migrate |
| ThreadPoolExecutor (stdlib) | async httpx | Only if the entire stack were greenfield async — a full rewrite, not a hardening change |
| Custom ConsecutiveFailureBreaker | pybreaker 1.4.1 | Only if you need distributed/Redis-backed state across multiple processes |
| Custom ConsecutiveFailureBreaker | circuitbreaker 2.1.3 | Only if wrapping individual function calls at the decorator level (microservice pattern) |
| vcrpy 8.1.1 + pytest-recording 0.13.4 | respx 0.23.1 | When you want to mock specific httpx request patterns in unit tests (not for the integration cassette) |
| vcrpy 8.1.1 + pytest-recording 0.13.4 | pytest-httpx 0.36.2 | When you want fixture-based httpx mocking in unit tests (not for the integration cassette) |
| Intermediate-file pattern for EPUB | zipstream-new | Never for EPUB specifically — EPUB ZIP structure requirements make custom streaming fragile |
| Ratio heuristic in-process | Trafilatura / Readability | Never here — these are general article extractors, not chapter-extraction validators |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| bleach | Officially deprecated Jan 2023; depends on unmaintained html5lib; still gets Python compat updates but is a dead end | nh3 0.3.5 |
| async httpx / asyncio.gather for parallelism | Requires full pipeline rewrite (client, jobs, all API modules, JobRunner); disproportionate to a hardening milestone | concurrent.futures.ThreadPoolExecutor with existing sync RateLimitedClient |
| pybreaker / circuitbreaker / purgatory | All three are designed for decorator-based, per-function-call circuit breaking; don't fit the "N consecutive loop failures" pattern; add a dep for 20 lines of logic | 30-line ConsecutiveFailureBreaker in the codebase |
| pytest-vcr | Incompatible with pytest-recording; fewer features (no --record-mode CLI flag); already superseded by pytest-recording which uses the same vcrpy underneath | pytest-recording 0.13.4 |
| zipstream / custom EPUB ZIP assembly | EPUB ZIP has strict ordering requirements (uncompressed mimetype first, specific directory layout) that custom streaming would need to replicate; fragile and equivalent to reimplementing ebooklib | ebooklib 0.20 with intermediate-file pattern |
| html.escape() on paragraph HTML | Escapes all HTML entities — destroys formatting tags (`<b>`, `<i>`, `<a>`) — wrong tool for sanitizing HTML fragments | nh3.clean() with an explicit allowlist |

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| nh3 0.3.5 | Python >=3.8; ebooklib 0.20 | Outputs clean UTF-8 HTML fragments; no EPUB-specific issues |
| vcrpy 8.1.1 | httpx >=0.25 (current project uses >=0.27) | v8.0.0 rewrote httpx support via httpcore patching; cassettes from vcrpy <6.0 may need re-recording |
| pytest-recording 0.13.4 | vcrpy >=4.0, pytest >=6.0 | Incompatible with pytest-vcr — must uninstall pytest-vcr first |
| concurrent.futures.ThreadPoolExecutor | Python 3.11+ stdlib | No new dep; existing TokenBucket is already thread-safe |
| ebooklib 0.20 | Python 3.9-3.13 | Final release (Oct 2025); actively installed; no streaming API but intermediate-file pattern avoids OOM |

---

## Sources

- PyPI nh3: https://pypi.org/project/nh3/ — version 0.3.5, April 25 2026 (verified)
- PyPI bleach: https://pypi.org/project/bleach/ — version 6.3.0, deprecated notice confirmed (verified)
- nh3 API docs: https://nh3.readthedocs.io/en/latest/ — `clean()` signature with all parameters (verified)
- PyPI vcrpy: https://pypi.org/project/vcrpy/ — version 8.1.1, January 4 2026 (verified via pip)
- vcrpy changelog: https://vcrpy.readthedocs.io/en/latest/changelog.html — v8.0.0 httpcore rewrite for httpx (verified)
- vcrpy installation docs: https://vcrpy.readthedocs.io/en/latest/installation.html — httpx listed as supported library (verified)
- PyPI pytest-recording: https://pypi.org/project/pytest-recording/ — version 0.13.4, May 8 2025 (verified via pip)
- PyPI pybreaker: https://pypi.org/project/pybreaker/ — version 1.4.1, Sep 2025 (verified via pip)
- PyPI circuitbreaker: version 2.1.3 (verified via pip)
- PyPI ebooklib: https://pypi.org/project/EbookLib/ — version 0.20, October 26 2025 (verified)
- PyPI respx: version 0.23.1 (verified via pip)
- PyPI pytest-httpx: version 0.36.2 (verified via pip)
- Existing codebase: `local_story_archive/client.py` — TokenBucket is already threading.Lock-safe (confirmed by source read)
- Existing codebase: `local_story_archive/api/user.py` — library endpoint used for cookie validation recommendation (confirmed by source read)
- PROJECT.md constraint: "Concurrency: Stay single-process. In-story parallelism via `concurrent.futures.ThreadPoolExecutor`; rate limit shared via the existing `RateLimitedClient` token bucket." (directly constrains the parallelism decision)

---
*Stack research for: Local Story Archive hardening milestone*
*Researched: 2026-05-03*
