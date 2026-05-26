<!-- GSD:project-start source:PROJECT.md -->
## Project

**Wattpad Crawler**

A personal Python tool for archiving Wattpad stories — fetches metadata, chapters, and comments via Wattpad's unofficial API, stores them on disk in an append-only layout, and renders EPUB / HTML / TXT artifacts. Ships both a CLI (`wattpad-crawler`) and a local FastAPI web UI (dashboard, library browser, reader, live progress) for solo use on the owner's machine.

**Core Value:** Reliably preserve Wattpad stories the user cares about — without silent failures, dead cookies, or broken scrapers wasting hours of archive time.

### Constraints

- **Tech stack**: Python 3.11+ — Already chosen; `pyproject.toml` enforces. Sticks with stdlib + minimal deps where possible.
- **Dependencies (allowed additions)**: `bleach` or `nh3` for sanitization. Test sanitizer choice on EPUB output before committing.
- **Concurrency**: Stay single-process. In-story parallelism via `concurrent.futures.ThreadPoolExecutor`; rate limit shared via the existing `RateLimitedClient` token bucket.
- **Backwards compatibility**: Existing archives on disk must continue to work — no schema breaks to `_state.sqlite` without a migration; no changes to story-directory layout.
- **Wattpad ToS**: Tool already violates ToS by scraping. Don't add anything that increases visibility (e.g., higher default rates, distinguishing user-agent strings).
- **Audience**: Single user. Don't add multi-user, sharing, or onboarding code.
- **Platform**: Windows-first dev environment but should run on macOS/Linux unchanged. No platform-specific paths or APIs.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.11+ - Entire codebase (CLI, web UI, API clients, rendering)
## Runtime
- Python 3.11+ (as specified in `pyproject.toml` `requires-python = ">=3.11"`)
- pip (with `pyproject.toml` as single source of truth)
- Lockfile: Not present (uses `pyproject.toml` PEP 517/518 format)
## Frameworks
- FastAPI 0.110+ - REST API and web UI backend (`wattpad_crawler/web/app.py`)
- Uvicorn 0.27+ - ASGI server for running web UI and CLI `serve` subcommand (`wattpad_crawler/cli.py:line 101`)
- Jinja2 3.1+ - HTML templating for web UI pages (`wattpad_crawler/web/app.py:line 18`)
- httpx 0.27+ - Async/sync HTTP requests with retry and timeout support (`wattpad_crawler/client.py`)
- BeautifulSoup4 4.12+ - Parsing Wattpad chapter HTML (`wattpad_crawler/scrape/chapter_html.py`)
- lxml 5.0+ - Fast HTML/XML parsing backend for BeautifulSoup
- ebooklib 0.18+ - EPUB file creation (`wattpad_crawler/render/epub.py`)
- sse-starlette 2.0+ - Server-Sent Events for live progress streaming (`wattpad_crawler/web/routes.py`)
- pytest 8.0+ - Test runner and framework
- pytest-vcr 1.0.2+ - HTTP cassette recording for repeatable tests
- vcrpy 6.0+ - Records HTTP interactions for mocking external API calls
- ruff 0.5+ - Fast Python linter and formatter
- hatchling - Build backend for PEP 517/518 builds
## Key Dependencies
- httpx - Handles all Wattpad API requests with rate limiting and retry logic built atop it
- FastAPI - Serves the local web UI (library browser, reader, progress streaming)
- BeautifulSoup4 + lxml - Extracts chapter content and structure from HTML
- sqlite3 (stdlib) - Local append-only manifest database at `wattpad-archive/_state.sqlite`
- pathlib (stdlib) - File system operations and archive directory management
- tomllib (stdlib) - Parses TOML configuration (`wattpad-archive/_config.toml`)
## Configuration
- No environment variables required; configuration stored in TOML file
- Cookie-based authentication: Wattpad session token stored in `_config.toml` (see README setup instructions)
- `pyproject.toml` (`D:\Dev\Wattpad Crawler\pyproject.toml`) - Project metadata, dependencies, build config, tool settings (pytest, ruff)
- `_config.toml` - Generated at runtime in archive output directory (`wattpad-archive/_config.toml`)
- `pyproject.toml` - Uses `hatchling` as build backend
- Wheel configuration at `[tool.hatch.build.targets.wheel]` forces inclusion of web templates and static files
## Platform Requirements
- Python 3.11+ interpreter
- Virtual environment (`.venv/`)
- pip (for installing dependencies)
- Python 3.11+ runtime
- Local filesystem storage for archive output (`wattpad-archive/` directory)
- Minimal resources: Single-threaded main process with optional worker threads per story (configurable via `workers_per_story`)
- Optional: IPv4 network access to Wattpad API endpoints (required only for archiving; not for serving cached content)
## Network & External APIs
- `https://www.wattpad.com/api/v3/stories/{story_id}` - Fetch story metadata and part list
- `https://www.wattpad.com/api/v3/users/{username}/library` - User's library (paginated)
- `https://www.wattpad.com/api/v3/users/{username}/lists` - User's reading lists
- `https://www.wattpad.com/api/v3/lists/{list_id}/stories` - Stories in a reading list (paginated)
- `https://www.wattpad.com/api/v3/parts/{part_id}/comments` - Chapter comments (paginated, supports inline and end-of-chapter)
- Token bucket implementation in `RateLimitedClient` (`wattpad_crawler/client.py`)
- Default: 2.0 requests/sec (configurable)
- Implements exponential backoff (up to 16s) for 5xx errors
- Respects HTTP 429 (Too Many Requests) with Retry-After header parsing
## Database
- SQLite 3 (stdlib module) - Local state and archive manifest
- Database: `wattpad-archive/_state.sqlite`
- Schema: Tables for `stories`, `parts` (chapters), and `runs`
- Features:
## Output Files
- Location: `wattpad-archive/` (configurable via `--output` CLI flag)
- Manifest: `_state.sqlite` (SQLite database)
- Config: `_config.toml` (TOML file)
- Stories: `stories/{author}/{story_id}_{slug}/`
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Lowercase with underscores: `client.py`, `chapter_html.py`, `api_comments.py`
- Test files: `test_<module>.py` (e.g., `test_client.py`, `test_config.py`)
- Web route files: `routes.py`, `app.py` (per-module organization)
- Lowercase with underscores: `build_client()`, `fetch_story()`, `parse_story()`
- Private functions: Leading underscore `_default_deps()`, `_tmp_path()`, `_safe_path_part()`
- Predicates: Prefixed with `is_` or use clear verb forms: `is_disconnected()` (from Starlette)
- Lowercase with underscores: `story_id`, `output_dir`, `rate_limit_per_sec`
- Type aliases and status literals: `PartStatus`, `StoryStatus` (Literal types)
- Constants: Uppercase with underscores: `STORY_FIELDS`, `STORY_URL`
- Single-letter variables only in loops/iterables: `for i, p in enumerate(...)`
- Dataclasses use CapitalCase: `Config`, `Story`, `Part`, `Comment`
- Custom exception classes use CapitalCase: `ConfigError`, `ResolveError`
- Literal type aliases use CapitalCase: `PartStatus`, `StoryStatus`
## Code Style
- Tool: `ruff` (not black - ruff does formatting via `ruff format`)
- Line length: 100 characters (configured in `pyproject.toml`)
- Python version target: 3.11+
- Tool: `ruff` with `ruff check`
- Rules selected: `["E", "F", "I", "UP", "W"]`
- Order: Standard library → Third-party → First-party (configured in `pyproject.toml`)
- First-party known modules: `wattpad_crawler`
- Grouped logically with blank lines between groups
- Imports at module level, not inside functions (except async context manager imports in `web/routes.py`)
## Type Hints
- Comprehensive type annotations on all functions and dataclass fields
- Union types use pipe syntax: `Exception | None` (Python 3.10+ style, required for 3.11)
- Optional values expressed as `T | None` not `Optional[T]`
- Return types always specified
- Use `Callable[[ArgTypes], ReturnType]` from `collections.abc`
- See `JobDeps` dataclass in `wattpad_crawler/jobs.py` for dependency injection pattern
## Error Handling
- Define custom exception classes that inherit from `Exception`
- Example: `ConfigError` in `wattpad_crawler/config.py`, `ResolveError` in `wattpad_crawler/jobs.py`
- Custom exceptions used for domain-specific errors (config parsing, story resolution)
- Eager validation at boundaries (config loading, API response parsing)
- Raise `ValueError` for invalid API responses with descriptive messages
- Validate preconditions in public methods with clear error messages
## Logging
- Use `logger.info()` for significant events (archiving start, etc.)
- Use `logger.warning()` for recoverable failures (cover fetch failure, 429 rate limit)
- Use `logger.exception()` for unhandled exceptions (logs traceback automatically)
- Format: `"%(asctime)s %(levelname)s %(name)s: %(message)s"`
- Set in `wattpad_crawler/cli.py` via `logging.basicConfig()`
- Verbosity controlled by `--verbose` flag (DEBUG vs INFO)
## Comments
- Document non-obvious behavior: "Per-process, per-thread tmp filename — avoids collisions..."
- Explain architectural decisions: "Indirection layer so tests can inject fakes" (JobDeps dataclass)
- Add implementation notes: "We'd rather lose one weird character than fail to archive a comment"
- Link to specifications: "Spec note: ASCII-only slug is acceptable for v1; this test documents that"
- One-liner docstrings for simple functions
- Full docstrings for classes and complex methods
- Docstrings on public API only
## Async vs Sync
- `wattpad_crawler/web/routes.py`: FastAPI route handlers are async (required by FastAPI)
- Async patterns include form parsing: `form = await request.form()`
- Async request disconnection checks: `if await request.is_disconnected()`
- Thread-to-asyncio bridging: `await asyncio.sleep(0.25)` for polling integration
- Core crawling/archiving logic is I/O-bound but uses `httpx.Client` (synchronous httpx)
- Easier to reason about threading for rate limiting (TokenBucket uses `threading.Lock`)
- Web UI uses async for server efficiency but delegates to sync thread pools
## Context Managers and Cleanup
## Dataclasses
- Use `@dataclass` decorator for simple data structures
- Use `frozen=True` for immutable configs: `@dataclass(frozen=True)`
- Frozen dataclasses prevent accidental mutation (tested in `test_config.py`)
- Field defaults via `default_factory` for mutable defaults
## Testing Interfaces
- Use dataclass wrappers for testable dependencies
- See `JobDeps` in `wattpad_crawler/jobs.py`
- Allows tests to inject mocks without monkeypatching
- Default implementation provided by `_default_deps()`
## Path Handling
- Use `pathlib.Path` everywhere (not `os.path`)
- Path operations on `Path` objects: `.read_text()`, `.write_text()`, `.exists()`, `.mkdir()`
- Type hints: `output_dir: Path`, `config_path: Path`
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- **Fetch → Parse → Store → Render:** Each story archival follows a distinct, testable pipeline
- **Manifest-driven state management:** SQLite manifest (`_state.sqlite`) tracks story/part status across CLI and web runs
- **Pluggable dependency injection:** JobDeps allows test doubles for external API calls without mocking frameworks
- **Threading for web concurrency:** Background JobRunner threads execute archive jobs while web serves requests
- **Append-only archive:** Files are never deleted, only added or updated; atomically written to prevent corruption
## Layers
- Purpose: Command parsing and HTTP routing; entry points for user interaction
- Location: `wattpad_crawler/cli.py`, `wattpad_crawler/web/app.py`, `wattpad_crawler/web/routes.py`
- Contains: Argument parser, FastAPI route handlers, template rendering
- Depends on: Config, JobManager/JobRunner (web only), archive jobs
- Used by: End users via terminal or browser
- Purpose: Job lifecycle management, library browsing, and live progress streaming
- Location: `wattpad_crawler/web/runner.py`, `wattpad_crawler/web/library_browser.py`
- Contains: JobManager (in-memory job registry), JobRunner (thread pool), LibraryEntry scanner
- Depends on: Archive store, Manifest for reading
- Used by: Web route handlers
- Purpose: Execute the core fetch → parse → store → render workflow
- Location: `wattpad_crawler/jobs.py`
- Contains: `archive_story()`, `archive_many()`, story/URL resolution, progress callbacks
- Depends on: API clients, Manifest, Store, Render modules
- Used by: CLI (main thread), Web (background threads via JobRunner)
- Purpose: Fetch story metadata, chapters, comments from Wattpad's unofficial API
- Location: `wattpad_crawler/api/story.py`, `wattpad_crawler/api/user.py`, `wattpad_crawler/api/comments.py`
- Contains: HTTP fetch + response parsing for Wattpad API v3 endpoints
- Depends on: RateLimitedClient
- Used by: Archive pipeline
- Purpose: Rate-limited HTTP requests with session cookie auth
- Location: `wattpad_crawler/client.py`
- Contains: RateLimitedClient (token bucket rate limiter) wrapping httpx
- Depends on: Config (for cookie, rate limit settings), httpx library
- Used by: API layer, archive jobs
- Purpose: Load and validate runtime settings from `_config.toml`
- Location: `wattpad_crawler/config.py`
- Contains: Config dataclass, TOML parsing, defaults
- Depends on: tomllib (stdlib), Path
- Used by: CLI, Web app initialization
- Purpose: Type-safe representations of domain objects
- Location: `wattpad_crawler/models.py`
- Contains: Story, Part, Comment dataclasses with Literal status types
- Depends on: stdlib only
- Used by: API parsers, archive pipeline, storage layer
- Purpose: Persistent tracking and file I/O for archived content
- Location: `wattpad_crawler/archive/state.py` (Manifest), `wattpad_crawler/archive/store.py`
- Contains: Manifest (SQLite CRUD), atomic file write utilities, story directory layout
- Depends on: sqlite3, models
- Used by: Archive pipeline, web library scanner
- Purpose: Parse chapter HTML, extract text, generate EPUB/HTML/TXT artifacts
- Location: `wattpad_crawler/scrape/chapter_html.py`, `wattpad_crawler/render/*.py`
- Contains: BeautifulSoup extraction (paragraph IDs, images, text), EbookLib EPUB generation, HTML/TXT renderers
- Depends on: beautifulsoup4, lxml, ebooklib, json
- Used by: Archive pipeline
## Data Flow
- Wrapper around `archive_story()`
- Emits "batch.start" with story_ids
- Sequentially calls `archive_story()` for each ID with same client/manifest
- Collects results {story_id → status}
- Emits "batch.done" with results
- Route `/library` calls `scan_library(output_dir)`
- Walks `stories/<author>/<id>_<slug>/` directories
- For each dir with `metadata.json`, creates LibraryEntry
- Entries sorted (author, title) and returned to template
- Cover served via `/library/cover/{author}/{dir_name}` route
- `/read/{author}/{dir_name}` → Table of contents (parts list)
- `/read/{author}/{dir_name}/{ordinal}` → Single chapter view
- Reads metadata.json + part text files from disk
- Computes prev/next chapter ordinals for navigation
- Artifacts (EPUB/HTML/TXT) served from `output/` via `/library/output/{author}/{dir_name}/{fmt}`
- **Manifest (_state.sqlite):**
- **File System:**
- **Web Job Memory (JobManager):**
## Key Abstractions
- Purpose: Immutable domain objects, serializable to JSON
- Examples: `models.Story`, `models.Part`, `models.Comment`
- Pattern: @dataclass with Literal type hints for enums
- Purpose: SQLite-backed story/part state CRUD + atomic transactions
- Examples: `archive.state.Manifest`
- Pattern: Connect → CRUD methods → close (context manager support)
- Purpose: httpx wrapper with token bucket rate limiting + cookie auth
- Examples: `client.RateLimitedClient`
- Pattern: Thread-safe token bucket; blocks on take() when rate exceeded
- Purpose: Structured output from HTML parsing; preserves paragraph IDs and images
- Examples: `scrape.chapter_html.ChapterContent`
- Pattern: @dataclass with text (plain), paragraphs (list of dicts), images (list of URLs)
- Purpose: Dependency injection for archive pipeline (enables testing without mocking)
- Examples: `jobs.JobDeps`, `web.runner.JobWork`
- Pattern: Callables passed as function args; defaults provided by `_default_deps()`
- Purpose: Lightweight view of a single archived story for web display
- Examples: `web.library_browser.LibraryEntry`
- Pattern: @dataclass extracted from metadata.json + filesystem checks
## Entry Points
- Location: `cli.py:main()`
- Triggers: User runs `wattpad-crawler [subcommand]`
- Responsibilities:
- Location: `web/app.py:build_app()` → `cli.py` calls uvicorn.run()
- Triggers: User navigates to http://127.0.0.1:8000/
- Responsibilities:
- Location: `web/routes.py` (router module)
- Handlers:
## Error Handling
## Cross-Cutting Concerns
- Root logger setup in `cli.py:_setup_logging()`
- Level: DEBUG (if -v flag), else INFO
- All modules define `logger = logging.getLogger(__name__)`
- Archive pipeline emits structured progress events (kind + dict data)
- URL resolution: `resolve_story_id()` validates story_id format
- File paths: `store._safe_path_part()` sanitizes author/part_id, `_resolve_story_dir()` rejects path traversal
- Config: `load_config()` validates TOML syntax, rate_limit > 0, workers >= 1
- API responses: Explicit None checks (e.g., story.get("id") or raise ValueError)
- Set in RateLimitedClient from cfg.cookie
- Stored in `_config.toml` (user-provided, never committed)
- Web UI `/setup` route allows updating without terminal
- Optional (public stories work without cookie)
- **CLI mode:** Single-threaded; all work in main thread
- **Web mode:** Background JobRunner threads (one per submitted job)
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.Codex/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-Codex-profile` -- do not edit manually.
<!-- GSD:profile-end -->
