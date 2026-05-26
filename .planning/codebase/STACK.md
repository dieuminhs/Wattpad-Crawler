# Technology Stack

**Analysis Date:** 2026-05-03

## Languages

**Primary:**
- Python 3.11+ - Entire codebase (CLI, web UI, API clients, rendering)

## Runtime

**Environment:**
- Python 3.11+ (as specified in `pyproject.toml` `requires-python = ">=3.11"`)

**Package Manager:**
- pip (with `pyproject.toml` as single source of truth)
- Lockfile: Not present (uses `pyproject.toml` PEP 517/518 format)

## Frameworks

**Core Web/API:**
- FastAPI 0.110+ - REST API and web UI backend (`local_story_archive/web/app.py`)
- Uvicorn 0.27+ - ASGI server for running web UI and CLI `serve` subcommand (`local_story_archive/cli.py:line 101`)
- Jinja2 3.1+ - HTML templating for web UI pages (`local_story_archive/web/app.py:line 18`)

**HTTP Client:**
- httpx 0.27+ - Async/sync HTTP requests with retry and timeout support (`local_story_archive/client.py`)

**HTML Processing:**
- BeautifulSoup4 4.12+ - Parsing Wattpad chapter HTML (`local_story_archive/scrape/chapter_html.py`)
- lxml 5.0+ - Fast HTML/XML parsing backend for BeautifulSoup

**E-book Generation:**
- ebooklib 0.18+ - EPUB file creation (`local_story_archive/render/epub.py`)

**Real-time Server Communication:**
- sse-starlette 2.0+ - Server-Sent Events for live progress streaming (`local_story_archive/web/routes.py`)

**Testing:**
- pytest 8.0+ - Test runner and framework
- pytest-vcr 1.0.2+ - HTTP cassette recording for repeatable tests
- vcrpy 6.0+ - Records HTTP interactions for mocking external API calls

**Development/Linting:**
- ruff 0.5+ - Fast Python linter and formatter
  - Line length: 100 characters
  - Target: Python 3.11
  - Rules: E (pycodestyle errors), F (Pyflakes), I (isort), UP (modernize), W (warnings)

**Build:**
- hatchling - Build backend for PEP 517/518 builds
  - Packages: `local_story_archive` module
  - Includes web assets: `local_story_archive/web/templates` and `local_story_archive/web/static` directories

## Key Dependencies

**Critical:**
- httpx - Handles all Wattpad API requests with rate limiting and retry logic built atop it
- FastAPI - Serves the local web UI (library browser, reader, progress streaming)
- BeautifulSoup4 + lxml - Extracts chapter content and structure from HTML

**Infrastructure:**
- sqlite3 (stdlib) - Local append-only manifest database at `wattpad-archive/_state.sqlite`
- pathlib (stdlib) - File system operations and archive directory management
- tomllib (stdlib) - Parses TOML configuration (`wattpad-archive/_config.toml`)

## Configuration

**Environment:**
- No environment variables required; configuration stored in TOML file
- Cookie-based authentication: Wattpad session token stored in `_config.toml` (see README setup instructions)

**Configuration Files:**
- `pyproject.toml` (`D:\Dev\Local Story Archive\pyproject.toml`) - Project metadata, dependencies, build config, tool settings (pytest, ruff)
- `_config.toml` - Generated at runtime in archive output directory (`wattpad-archive/_config.toml`)
  - Fields: `cookie` (Wattpad session token), `rate_limit_per_sec` (default 2.0), `workers_per_story` (default 3), `user_agent`

**Build Configuration:**
- `pyproject.toml` - Uses `hatchling` as build backend
- Wheel configuration at `[tool.hatch.build.targets.wheel]` forces inclusion of web templates and static files

## Platform Requirements

**Development:**
- Python 3.11+ interpreter
- Virtual environment (`.venv/`)
- pip (for installing dependencies)

**Production:**
- Python 3.11+ runtime
- Local filesystem storage for archive output (`wattpad-archive/` directory)
- Minimal resources: Single-threaded main process with optional worker threads per story (configurable via `workers_per_story`)
- Optional: IPv4 network access to Wattpad API endpoints (required only for archiving; not for serving cached content)

## Network & External APIs

**Wattpad API Endpoints:**
- `https://www.wattpad.com/api/v3/stories/{story_id}` - Fetch story metadata and part list
- `https://www.wattpad.com/api/v3/users/{username}/library` - User's library (paginated)
- `https://www.wattpad.com/api/v3/users/{username}/lists` - User's reading lists
- `https://www.wattpad.com/api/v3/lists/{list_id}/stories` - Stories in a reading list (paginated)
- `https://www.wattpad.com/api/v3/parts/{part_id}/comments` - Chapter comments (paginated, supports inline and end-of-chapter)

**Rate Limiting:**
- Token bucket implementation in `RateLimitedClient` (`local_story_archive/client.py`)
- Default: 2.0 requests/sec (configurable)
- Implements exponential backoff (up to 16s) for 5xx errors
- Respects HTTP 429 (Too Many Requests) with Retry-After header parsing

## Database

**Storage:**
- SQLite 3 (stdlib module) - Local state and archive manifest
- Database: `wattpad-archive/_state.sqlite`
- Schema: Tables for `stories`, `parts` (chapters), and `runs`
- Features:
  - Foreign key enforcement enabled
  - WAL (Write-Ahead Logging) mode for concurrent read/write access
  - Atomic writes to prevent corruption

## Output Files

**Archive Structure:**
- Location: `wattpad-archive/` (configurable via `--output` CLI flag)
- Manifest: `_state.sqlite` (SQLite database)
- Config: `_config.toml` (TOML file)
- Stories: `stories/{author}/{story_id}_{slug}/`
  - `metadata.json` - Story metadata
  - `cover.jpg` - Story cover image
  - `parts/` - Chapter data (JSON, HTML, TXT per chapter)
  - `output/` - Rendered formats (EPUB, HTML, TXT)

---

*Stack analysis: 2026-05-03*
