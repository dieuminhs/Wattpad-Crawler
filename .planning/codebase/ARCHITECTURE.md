# Architecture

**Analysis Date:** 2026-05-03

## Pattern Overview

**Overall:** Layered + Pipeline

The codebase follows a **layered architecture** with a clear separation between CLI/Web frontends, business logic, and data persistence. At its core is a **sequential archival pipeline** that fetches story metadata, chapters, comments, and renders multiple output formats.

**Key Characteristics:**
- **Fetch → Parse → Store → Render:** Each story archival follows a distinct, testable pipeline
- **Manifest-driven state management:** SQLite manifest (`_state.sqlite`) tracks story/part status across CLI and web runs
- **Pluggable dependency injection:** JobDeps allows test doubles for external API calls without mocking frameworks
- **Threading for web concurrency:** Background JobRunner threads execute archive jobs while web serves requests
- **Append-only archive:** Files are never deleted, only added or updated; atomically written to prevent corruption

## Layers

**CLI / Web Interface Layer:**
- Purpose: Command parsing and HTTP routing; entry points for user interaction
- Location: `wattpad_crawler/cli.py`, `wattpad_crawler/web/app.py`, `wattpad_crawler/web/routes.py`
- Contains: Argument parser, FastAPI route handlers, template rendering
- Depends on: Config, JobManager/JobRunner (web only), archive jobs
- Used by: End users via terminal or browser

**Web-Specific Components:**
- Purpose: Job lifecycle management, library browsing, and live progress streaming
- Location: `wattpad_crawler/web/runner.py`, `wattpad_crawler/web/library_browser.py`
- Contains: JobManager (in-memory job registry), JobRunner (thread pool), LibraryEntry scanner
- Depends on: Archive store, Manifest for reading
- Used by: Web route handlers

**Archive Pipeline Layer:**
- Purpose: Execute the core fetch → parse → store → render workflow
- Location: `wattpad_crawler/jobs.py`
- Contains: `archive_story()`, `archive_many()`, story/URL resolution, progress callbacks
- Depends on: API clients, Manifest, Store, Render modules
- Used by: CLI (main thread), Web (background threads via JobRunner)

**External API Layer:**
- Purpose: Fetch story metadata, chapters, comments from Wattpad's unofficial API
- Location: `wattpad_crawler/api/story.py`, `wattpad_crawler/api/user.py`, `wattpad_crawler/api/comments.py`
- Contains: HTTP fetch + response parsing for Wattpad API v3 endpoints
- Depends on: RateLimitedClient
- Used by: Archive pipeline

**HTTP Client Layer:**
- Purpose: Rate-limited HTTP requests with session cookie auth
- Location: `wattpad_crawler/client.py`
- Contains: RateLimitedClient (token bucket rate limiter) wrapping httpx
- Depends on: Config (for cookie, rate limit settings), httpx library
- Used by: API layer, archive jobs

**Configuration Layer:**
- Purpose: Load and validate runtime settings from `_config.toml`
- Location: `wattpad_crawler/config.py`
- Contains: Config dataclass, TOML parsing, defaults
- Depends on: tomllib (stdlib), Path
- Used by: CLI, Web app initialization

**Data Models:**
- Purpose: Type-safe representations of domain objects
- Location: `wattpad_crawler/models.py`
- Contains: Story, Part, Comment dataclasses with Literal status types
- Depends on: stdlib only
- Used by: API parsers, archive pipeline, storage layer

**Archive State & Storage Layer:**
- Purpose: Persistent tracking and file I/O for archived content
- Location: `wattpad_crawler/archive/state.py` (Manifest), `wattpad_crawler/archive/store.py`
- Contains: Manifest (SQLite CRUD), atomic file write utilities, story directory layout
- Depends on: sqlite3, models
- Used by: Archive pipeline, web library scanner

**Content Extraction & Rendering:**
- Purpose: Parse chapter HTML, extract text, generate EPUB/HTML/TXT artifacts
- Location: `wattpad_crawler/scrape/chapter_html.py`, `wattpad_crawler/render/*.py`
- Contains: BeautifulSoup extraction (paragraph IDs, images, text), EbookLib EPUB generation, HTML/TXT renderers
- Depends on: beautifulsoup4, lxml, ebooklib, json
- Used by: Archive pipeline

## Data Flow

**Archive Story Flow (CLI or Web):**

1. **URL → Story ID Resolution**
   - User provides story ID or full URL
   - `resolve_story_id()` in `jobs.py` extracts numeric story_id
   - Invalid input raises ResolveError (HTTP 400 in web)

2. **Fetch Story Metadata**
   - `api_story.fetch_story()` calls Wattpad API v3 `/stories/{id}` endpoint
   - RateLimitedClient enforces rate limit (default 2.0 req/sec)
   - Response parsed into Story dataclass with Part list

3. **Upsert to Manifest**
   - `manifest.upsert_story()` and `manifest.upsert_parts()` insert/update SQLite
   - Rows marked with initial status "pending"
   - Enables safe re-runs: skips already-done parts

4. **Write Story Metadata & Cover**
   - `store.write_story_metadata()` → `stories/<author>/<story_id>_<slug>/metadata.json`
   - `store.write_cover()` → `stories/<author>/<story_id>_<slug>/cover.jpg` (if present)
   - Atomic writes prevent partial file corruption

5. **Per-Chapter Pipeline (Sequential)**
   For each Part in story.parts:
   
   a. **Check Cache**
      - Query Manifest for existing part status
      - If status == "done" and body_hash matches, skip
      - Emit "part.skipped" event
   
   b. **Fetch Chapter HTML**
      - RateLimitedClient.get(part.url) → raw HTML
      - Store raw HTML for debugging/preservation
   
   c. **Extract Chapter Content**
      - `extract_chapter(raw_html)` via BeautifulSoup
      - Returns ChapterContent (text, paragraphs with IDs, image URLs)
      - Paragraphs preserve original data-p-id for comment linking
   
   d. **Fetch Inline & End Comments**
      - Two separate API calls (threaded? no—sequential in default_deps)
      - Comments recursively include reply threads
   
   e. **Store Part Files**
      - `store.write_part_files()` writes:
        - `{ordinal:02d}_{part_id}_{slug}.json` (ChapterContent metadata)
        - `{ordinal:02d}_{part_id}_{slug}.html` (raw HTML)
        - `{ordinal:02d}_{part_id}_{slug}.txt` (extracted text)
        - `{ordinal:02d}_{part_id}_comments-inline.json` (inline comment thread)
        - `{ordinal:02d}_{part_id}_comments-end.json` (end comment thread)
      - Atomic writes avoid half-written files
      - Body SHA256 hash stored in Manifest for deduplication
   
   f. **Update Part Status**
      - manifest.set_part_status() → "done" (or "failed" on exception)
      - Logs any errors for subsequent inspection

6. **Post-Archive Rendering**
   - After all parts succeed/fail, render phase begins
   - Three renderers (txt, html, epub) process `story_dir/parts/` → `story_dir/output/`
   - Each renderer independently reads metadata.json + part files, writes artifact
   - Render failures logged but don't block archive completion

7. **Emit Final Event**
   - "story.done" event marks completion
   - Web job sets status to "done" or "failed"

**Archive Many (Library/List):**

- Wrapper around `archive_story()`
- Emits "batch.start" with story_ids
- Sequentially calls `archive_story()` for each ID with same client/manifest
- Collects results {story_id → status}
- Emits "batch.done" with results

**Web Job Lifecycle:**

1. User submits form on dashboard
2. Route handler calls `JobManager.create()` → creates in-memory Job
3. Route calls `JobRunner.submit(job, work_callable)` → spawns daemon thread
4. Work function:
   - Opens new RateLimitedClient + Manifest (isolated from main)
   - Calls archive_story/archive_many with job.emit as progress callback
   - Closes client/manifest in finally block
5. Browser polls `/jobs/{job_id}/stream` (Server-Sent Events)
6. JobRunner catches exceptions, sets job.status to "failed"

**Library Browse:**

- Route `/library` calls `scan_library(output_dir)`
- Walks `stories/<author>/<id>_<slug>/` directories
- For each dir with `metadata.json`, creates LibraryEntry
- Entries sorted (author, title) and returned to template
- Cover served via `/library/cover/{author}/{dir_name}` route

**Reader Routes:**

- `/read/{author}/{dir_name}` → Table of contents (parts list)
- `/read/{author}/{dir_name}/{ordinal}` → Single chapter view
- Reads metadata.json + part text files from disk
- Computes prev/next chapter ordinals for navigation
- Artifacts (EPUB/HTML/TXT) served from `output/` via `/library/output/{author}/{dir_name}/{fmt}`

**State Management:**

- **Manifest (_state.sqlite):**
  - Single source of truth for "what's been archived"
  - Tracks story/part status: pending → in_progress → done (or failed)
  - Part body_hash prevents re-downloading identical content
  - WAL mode allows concurrent web reads + CLI writes
- **File System:**
  - Canonical archive: `stories/<author>/<story_id>_<slug>/`
  - Atomic writes prevent corruption on Ctrl-C (not power-loss safe)
  - Append-only: files never deleted, only added/updated
- **Web Job Memory (JobManager):**
  - Ephemeral: lost on web app restart
  - Holds last 10 jobs for dashboard display
  - Real state lives in Manifest + filesystem

## Key Abstractions

**Story & Part Models:**
- Purpose: Immutable domain objects, serializable to JSON
- Examples: `models.Story`, `models.Part`, `models.Comment`
- Pattern: @dataclass with Literal type hints for enums

**Manifest:**
- Purpose: SQLite-backed story/part state CRUD + atomic transactions
- Examples: `archive.state.Manifest`
- Pattern: Connect → CRUD methods → close (context manager support)

**RateLimitedClient:**
- Purpose: httpx wrapper with token bucket rate limiting + cookie auth
- Examples: `client.RateLimitedClient`
- Pattern: Thread-safe token bucket; blocks on take() when rate exceeded

**ChapterContent:**
- Purpose: Structured output from HTML parsing; preserves paragraph IDs and images
- Examples: `scrape.chapter_html.ChapterContent`
- Pattern: @dataclass with text (plain), paragraphs (list of dicts), images (list of URLs)

**JobDeps & JobWork:**
- Purpose: Dependency injection for archive pipeline (enables testing without mocking)
- Examples: `jobs.JobDeps`, `web.runner.JobWork`
- Pattern: Callables passed as function args; defaults provided by `_default_deps()`

**LibraryEntry:**
- Purpose: Lightweight view of a single archived story for web display
- Examples: `web.library_browser.LibraryEntry`
- Pattern: @dataclass extracted from metadata.json + filesystem checks

## Entry Points

**CLI (wattpad-crawler):**
- Location: `cli.py:main()`
- Triggers: User runs `wattpad-crawler [subcommand]`
- Responsibilities:
  - Parse arguments via argparse
  - Load config from `_config.toml`
  - Create RateLimitedClient + Manifest
  - Dispatch to archive_story(), archive_many(), status, or serve
  - For "serve": hand off to uvicorn (closes own client/manifest first)

**Web App (FastAPI):**
- Location: `web/app.py:build_app()` → `cli.py` calls uvicorn.run()
- Triggers: User navigates to http://127.0.0.1:8000/
- Responsibilities:
  - Mount static files, Jinja2 templates
  - Create JobManager + JobRunner
  - Include router from `web.routes`
  - Stash cfg, templates, job_manager on app.state for route access

**Web Routes:**
- Location: `web/routes.py` (router module)
- Handlers:
  - `GET /setup`, `POST /setup` — Cookie configuration
  - `GET /` — Dashboard with job list
  - `POST /jobs` — Submit archive job (story/library/list)
  - `GET /jobs/{job_id}`, `/jobs/{job_id}/stream` — Job detail + SSE stream
  - `GET /library` — Story grid view
  - `GET /library/cover/{author}/{dir_name}` — Cover image
  - `GET /read/{author}/{dir_name}` — TOC
  - `GET /read/{author}/{dir_name}/{ordinal}` — Chapter view
  - `GET /library/output/{author}/{dir_name}/{fmt}` — Artifact download

## Error Handling

**Strategy:** Fail gracefully, log extensively, never leave half-written files

**Patterns:**

1. **Archive Pipeline Errors:**
   - Part HTML fetch fails → log exception, mark part "failed" in Manifest, continue to next part
   - Render fails → log exception, emit "render.failed", continue rendering other formats
   - Entire story fails (missing API response) → exception propagates, story left "in_progress"

2. **Web Route Errors:**
   - HTTPException(400) for invalid input (missing cookie, bad URL)
   - HTTPException(404) for missing files (cover, story, chapter)
   - Path traversal checked via `is_relative_to()` before serving files
   - Job exceptions caught in JobRunner._run(), job marked "failed" with error message

3. **Manifest/Config Errors:**
   - ConfigError raised if TOML invalid or values out of range
   - Manifest.connect() raises RuntimeError if queries run before connect()
   - sqlite3 FK + WAL pragmas set at connect() to ensure data integrity

4. **Rate Limiting:**
   - RateLimitedClient.take() blocks until token available
   - Never raises; just sleeps and retries
   - Job timeout managed by caller (not RateLimitedClient)

## Cross-Cutting Concerns

**Logging:** stdlib logging module
- Root logger setup in `cli.py:_setup_logging()`
- Level: DEBUG (if -v flag), else INFO
- All modules define `logger = logging.getLogger(__name__)`
- Archive pipeline emits structured progress events (kind + dict data)

**Validation:** Early in pipeline
- URL resolution: `resolve_story_id()` validates story_id format
- File paths: `store._safe_path_part()` sanitizes author/part_id, `_resolve_story_dir()` rejects path traversal
- Config: `load_config()` validates TOML syntax, rate_limit > 0, workers >= 1
- API responses: Explicit None checks (e.g., story.get("id") or raise ValueError)

**Authentication:** Wattpad session cookie
- Set in RateLimitedClient from cfg.cookie
- Stored in `_config.toml` (user-provided, never committed)
- Web UI `/setup` route allows updating without terminal
- Optional (public stories work without cookie)

**Concurrency:**

- **CLI mode:** Single-threaded; all work in main thread
- **Web mode:** Background JobRunner threads (one per submitted job)
  - JobManager uses threading.Lock for job registry
  - Job itself uses threading.Lock for event list
  - Manifest uses sqlite3 WAL for concurrent reader/writer
  - No explicit thread pool; unlimited concurrent jobs (scaled by ulimit)

---

*Architecture analysis: 2026-05-03*
