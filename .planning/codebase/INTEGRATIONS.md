# External Integrations

**Analysis Date:** 2026-05-03

## APIs & External Services

**Wattpad Official API (Unofficial):**
- Wattpad API - Unofficial, undocumented Wattpad API for fetching story metadata, chapters, comments, and user library
  - SDK/Client: httpx (async-capable HTTP client)
  - Auth: Cookie-based (session token stored in `_config.toml`)
  - Endpoints implemented in: `local_story_archive/api/story.py`, `local_story_archive/api/user.py`, `local_story_archive/api/comments.py`

## Data Storage

**Databases:**
- SQLite 3 (local)
  - Path: `wattpad-archive/_state.sqlite`
  - Connection: Direct via Python `sqlite3` stdlib module
  - Purpose: Manifest database tracking story/chapter download status, metadata, and job run history
  - Client: None (raw SQL via `sqlite3.Connection`)
  - Schema initialization: `local_story_archive/archive/state.py:_SCHEMA`
  - Configuration:
    - Foreign key enforcement: `PRAGMA foreign_keys = ON`
    - Write-Ahead Logging (WAL) mode for concurrent read/write: `PRAGMA journal_mode = WAL`
    - Row factory set to `sqlite3.Row` for dict-like access

**File Storage:**
- Local filesystem only
  - Archive path: `wattpad-archive/stories/` (configurable via `--output` CLI argument)
  - No cloud storage integration
  - Atomic write implementation in `local_story_archive/archive/store.py` prevents corruption from process interruption

**Caching:**
- None (SQLite state database serves as cache/manifest)
- Already-downloaded chapters are skipped on re-runs via state tracking in SQLite

## Authentication & Identity

**Auth Provider:**
- Custom (Cookie-based session auth with Wattpad)
  - Implementation: User manually obtains session token from browser and stores in `_config.toml`
  - Token name: `cookie` field in `_config.toml`
  - Passed as: HTTP cookie with domain `wattpad.com` (set in `local_story_archive/client.py:line 15`)
  - Setup: See `README.md` lines 26-32 (DevTools → Cookies → copy `token` value)
  - No OAuth, API key, or service account mechanisms

## Monitoring & Observability

**Error Tracking:**
- None (no integration with Sentry, Rollbar, etc.)
- Errors are logged locally via Python `logging` module

**Logs:**
- Logging approach: Python `logging` module with handlers to stdout
  - Format: `"%(asctime)s %(levelname)s %(name)s: %(message)s"`
  - Set via `_setup_logging()` in `local_story_archive/cli.py:line 46`
  - Verbosity: Controlled by `-v` / `--verbose` CLI flag (DEBUG level if set, INFO by default)
  - Key loggers: Various modules log at appropriate levels (httpx, RateLimitedClient, API fetchers)

## CI/CD & Deployment

**Hosting:**
- Local development/personal use (runs on user's machine)
- No official deployment platform
- Manual deployment: User installs from git clone + `pip install -e .`
- Optional homelab deployment: CLI supports `--host 0.0.0.0 --port 8000` for network binding (see `README.md` lines 76-80)

**CI Pipeline:**
- None configured (no GitHub Actions, GitLab CI, etc.)
- Testing: Manual via `pytest` command or integration test cassettes with vcrpy

## Environment Configuration

**Configuration Files:**
- `_config.toml` - User settings (generated at `wattpad-archive/_config.toml`)
  - Required field: `cookie` (Wattpad session token, initially empty)
  - Optional fields:
    - `rate_limit_per_sec` (float, default 2.0)
    - `workers_per_story` (int, default 3)
    - `user_agent` (str, default `"local-story-archive/0.1 (+local archive tool)"`)
  - Parser: `tomllib.loads()` in `local_story_archive/config.py`
  - Default template: `_DEFAULT_TOML` in `local_story_archive/config.py:line 19`

**No Environment Variables:**
- No `.env` file support
- All configuration via TOML file

**Secrets Location:**
- Wattpad session cookie stored in plain text in `_config.toml`
- Warning: This file is unencrypted; user responsible for keeping it private
- No key management or secret rotation service

## Web UI & Streaming

**Server-Sent Events (SSE):**
- sse-starlette 2.0+ for real-time progress updates
  - Used in: `local_story_archive/web/routes.py` (job progress streaming)
  - Response type: `EventSourceResponse` from `sse_starlette.sse`
  - Purpose: Stream chapter/comment fetch progress to browser in real-time

**Templating:**
- Jinja2 3.1+ for HTML templates
  - Template directory: `local_story_archive/web/templates/`
  - Files included in wheel: `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`
  - Used for: Setup page, dashboard, library view, chapter reader

**Static Assets:**
- CSS/JavaScript in `local_story_archive/web/static/`
  - Served via `StaticFiles` (FastAPI middleware)
  - Mounted at: `/static`
  - Files included in wheel: Configured in `pyproject.toml`

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

## Story Cover Images

**Fetching:**
- Covers are downloaded from Wattpad's CDN and stored locally
- URL source: Story metadata API response (`cover_url` field from `api/v3/stories/{story_id}`)
- Storage: `wattpad-archive/stories/{author}/{story_id}_{slug}/cover.jpg`
- Format: JPEG
- Used in: EPUB generation (`local_story_archive/render/epub.py:line 22`), library browser UI

## Chapter HTML Parsing

**Source:**
- Each chapter has both structured JSON data and raw HTML scraped from Wattpad
- JSON: Contains chapter metadata + paragraph structure
- HTML: Original Wattpad chapter page HTML for reference
- Plain text: Extracted from HTML for reading/searching
- Parsing: BeautifulSoup4 with lxml backend (`local_story_archive/scrape/chapter_html.py`)

## Comment Fetching

**API:**
- `https://www.wattpad.com/api/v3/parts/{part_id}/comments` (paginated)
- Two types:
  - Inline comments: `?limit=100` (default, attached to paragraphs)
  - End-of-chapter comments: `?limit=100&forms=root`
- Implementation: `local_story_archive/api/comments.py`
- Pagination: Follows `nextUrl` chain with cycle detection and max page limit (200)

## Rendering Formats

**Output Formats:**
1. EPUB (e-book) - `ebooklib` library (`local_story_archive/render/epub.py`)
2. HTML - Custom renderer (`local_story_archive/render/html.py`)
3. Plain text - Custom renderer (`local_story_archive/render/txt.py`)

**Triggers:**
- Rendered on each archive run (incremental chapters, full story outputs)
- Not pre-generated; generated on-demand during archival

## Rate Limiting & Retry Logic

**Rate Limiting:**
- Implementation: Token bucket algorithm in `RateLimitedClient` class (`local_story_archive/client.py:lines 24-47`)
- Default: 2.0 requests/sec (configurable via `rate_limit_per_sec` in `_config.toml`)
- Thread-safe: Uses `threading.Lock()`

**Retry Logic:**
- Automatic retry on transient failures:
  - Network errors (httpx.RequestError): Exponential backoff (1s, 2s, 4s, 8s, 16s max)
  - HTTP 5xx errors: Exponential backoff
  - HTTP 429 (Too Many Requests): Respects Retry-After header (defaults to 60s, capped at 300s)
- Max attempts: Configurable (default 5 in `get()` calls)

## Platform Compatibility

**Operating Systems:**
- Windows (PowerShell and Bash): Activation scripts in `.venv\Scripts\Activate.ps1`
- macOS/Linux: Activation script at `.venv/bin/activate`
- File path handling: Uses `pathlib.Path` for cross-platform compatibility

---

*Integration audit: 2026-05-03*
