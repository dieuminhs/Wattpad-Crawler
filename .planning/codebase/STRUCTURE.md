# Codebase Structure

**Analysis Date:** 2026-05-03

## Directory Layout

```
Wattpad Crawler/
├── .git/                                # Git repository
├── .planning/                           # GSD planning documents (generated)
├── .venv/                               # Python virtual environment
├── .pytest_cache/                       # pytest cache
├── .ruff_cache/                         # ruff linter cache
├── docs/                                # Project documentation & planning specs
├── tests/                               # Test suite
│   ├── conftest.py                      # pytest fixtures
│   ├── fixtures/                        # Test data
│   │   ├── api_responses/               # Mocked Wattpad API responses
│   │   └── html_chapters/               # Sample chapter HTML for parsing tests
│   ├── unit/                            # Unit tests
│   │   ├── test_api_*.py
│   │   ├── test_chapter_html.py
│   │   ├── test_cli.py
│   │   ├── test_client.py
│   │   ├── test_config.py
│   │   ├── test_jobs.py
│   │   ├── test_library_browser.py
│   │   ├── test_render_*.py
│   │   ├── test_runner.py
│   │   ├── test_state.py
│   │   ├── test_store.py
│   │   └── test_web_routes.py
│   └── integration/                     # Integration tests (slow, vcr-recorded)
│       └── test_end_to_end.py           # Full archive pipeline test
├── wattpad-archive/                     # Default local archive directory (user-created)
│   ├── _state.sqlite                    # State manifest database
│   ├── _config.toml                     # Configuration (user's cookie, rate limits)
│   └── stories/                         # Archived stories
│       └── <author>/                    # Author directory (sanitized username)
│           └── <story_id>_<slug>/       # Story directory
│               ├── metadata.json        # Story metadata (title, author, tags, parts list)
│               ├── cover.jpg            # Cover image
│               ├── parts/                # Chapter files
│               │   ├── 01_<part_id>_<slug>.json
│               │   ├── 01_<part_id>_<slug>.html
│               │   ├── 01_<part_id>_<slug>.txt
│               │   ├── 01_<part_id>_comments-inline.json
│               │   ├── 01_<part_id>_comments-end.json
│               │   ├── 02_<part_id>_<slug>.json
│               │   └── ...
│               └── output/               # Rendered artifacts
│                   ├── <slug>.epub
│                   ├── <slug>.html
│                   └── <slug>.txt
├── wattpad_crawler/                     # Main source code package
│   ├── __init__.py
│   ├── cli.py                           # CLI entry point & argument parser
│   ├── client.py                        # RateLimitedClient (rate-limited HTTP)
│   ├── config.py                        # Config loading from TOML
│   ├── jobs.py                          # Archive pipeline (story/many, progress)
│   ├── models.py                        # Domain models (Story, Part, Comment)
│   ├── api/                             # Wattpad API fetchers
│   │   ├── __init__.py
│   │   ├── story.py                     # fetch_story() + parse_story()
│   │   ├── user.py                      # fetch_library(), fetch_list_story_ids()
│   │   └── comments.py                  # fetch_inline_comments(), fetch_end_comments()
│   ├── archive/                         # State & storage
│   │   ├── __init__.py
│   │   ├── state.py                     # Manifest (SQLite CRUD)
│   │   └── store.py                     # Atomic file I/O, directory layout
│   ├── scrape/                          # Content extraction
│   │   ├── __init__.py
│   │   └── chapter_html.py              # extract_chapter() (BeautifulSoup)
│   ├── render/                          # Output format rendering
│   │   ├── __init__.py
│   │   ├── txt.py                       # render_txt()
│   │   ├── html.py                      # render_html()
│   │   └── epub.py                      # render_epub() (EbookLib)
│   └── web/                             # FastAPI web UI
│       ├── __init__.py
│       ├── app.py                       # FastAPI app factory
│       ├── routes.py                    # Route handlers
│       ├── runner.py                    # JobManager + JobRunner (threading)
│       ├── library_browser.py           # scan_library() + LibraryEntry
│       ├── static/                      # CSS, JS assets
│       │   ├── .gitkeep
│       │   └── style.css
│       └── templates/                   # Jinja2 HTML templates
│           ├── .gitkeep
│           ├── base.html                # Layout wrapper
│           ├── setup.html               # Cookie config form
│           ├── dashboard.html           # Job submission + recent jobs
│           ├── job.html                 # Job detail page
│           ├── library.html             # Story grid
│           ├── reader.html              # Chapter viewer (TOC + chapter view)
│           └── ... (other templates)
├── .gitignore
├── README.md                            # User-facing documentation
└── pyproject.toml                       # Python package config, dependencies, scripts
```

## Directory Purposes

**wattpad_crawler/ (Source Root)**
- Purpose: Main package directory; contains all runnable code
- Python 3.11+ only; entry point via `wattpad_crawler.cli:main`

**wattpad_crawler/api/**
- Purpose: Wattpad API v3 client layer
- Contains:
  - `story.py`: Fetch story metadata + parse into Story/Part objects
  - `user.py`: Fetch user library + reading lists
  - `comments.py`: Fetch inline/end comments with reply threads
- Key pattern: All functions accept RateLimitedClient, return dataclasses or dicts
- Used by: `jobs.py` archive pipeline

**wattpad_crawler/archive/**
- Purpose: Persistent state tracking (Manifest) and atomic file I/O (Store)
- `state.py`:
  - Manifest class: SQLite context manager, CRUD for stories/parts
  - Schema: stories table (id, author, title, status), parts table (story_id, part_id, status, body_hash)
  - WAL mode for concurrent access (web reads while CLI writes)
- `store.py`:
  - Atomic write utilities (temp file → rename, PID/TID suffixed)
  - `story_dir()`: Compute canonical path from Story object
  - `write_story_metadata()`: JSON metadata to disk
  - `write_part_files()`: JSON/HTML/TXT per chapter + comment JSONs
  - `write_cover()`: Cover image binary
  - Safe path part sanitization (no path traversal)
- Used by: Archive pipeline, web library scanner

**wattpad_crawler/scrape/**
- Purpose: Parse HTML into structured chapter data
- `chapter_html.py`:
  - `extract_chapter()`: BeautifulSoup parser that finds chapter text + images + paragraph IDs
  - Returns ChapterContent dataclass (text, paragraphs list, images list)
  - Paragraphs include data-p-id for linking comments
- Dependency: beautifulsoup4, lxml
- Used by: Archive pipeline (per-chapter)

**wattpad_crawler/render/**
- Purpose: Generate output artifacts (EPUB, HTML, TXT) from archived parts
- `txt.py`: Concatenate chapter text files into single TXT
- `html.py`: Generate single-file HTML with styling (chapters as sections)
- `epub.py`: Build EPUB via EbookLib (chapters as XHTML chapters)
- Pattern: All take story_dir Path, read metadata.json + part files, write to output/
- Used by: Archive pipeline (post-fetch, all formats)

**wattpad_crawler/web/**
- Purpose: FastAPI-based local web UI
- `app.py`: FastAPI instance factory; mounts static/templates, includes router, stashes state
- `routes.py`: 10+ route handlers (setup, dashboard, library, reader, jobs)
- `runner.py`:
  - JobManager: In-memory registry of submitted jobs (thread-safe)
  - JobRunner: Background thread pool (one thread per job)
  - Job dataclass: status, events, progress callbacks
- `library_browser.py`: scan_library() walks filesystem, returns LibraryEntry list
- static/: CSS (style.css)
- templates/: Jinja2 HTML files (base.html layout, 6 content templates)
- Used by: End user via browser at http://127.0.0.1:8000

**tests/**
- Purpose: Test suite (unit + integration)
- `conftest.py`: Pytest fixtures (fixtures_dir, output_dir temp)
- `fixtures/api_responses/`: Mocked Wattpad API JSON responses (story, library, etc.)
- `fixtures/html_chapters/`: Sample Wattpad chapter HTML (for extract_chapter() tests)
- `unit/`: 21 unit test modules (one per source module)
- `integration/`: VCR-recorded end-to-end test (slow; marked with @pytest.mark.live)
- Test data strategy: Fixtures are fixtures/ subdirs; temp directories created via conftest

**docs/**
- Purpose: Project documentation (user guides, specs, planning)
- Contents: README in root, design specs in docs/superpowers/

**.planning/**
- Purpose: GSD-generated analysis documents (created by /gsd-map-codebase)
- Contents: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md

**wattpad-archive/** (User-created at runtime)
- Purpose: Default local archive directory (respects --output flag)
- Structure: Mirrors git layout for stories; _config.toml and _state.sqlite at root
- Not committed to git (in .gitignore)

## Key File Locations

**Entry Points:**

- `wattpad_crawler/cli.py`: CLI entry point (argparse, subcommands: story, library, list, url, status, serve)
- `wattpad_crawler/web/app.py`: Web app factory (FastAPI instance)
- `wattpad_crawler/__init__.py`: Package init (empty)

**Configuration:**

- `wattpad_crawler/config.py`: Config dataclass + load_config()
- `wattpad-archive/_config.toml`: User's cookie + rate limits (created on first run)

**Core Pipeline:**

- `wattpad_crawler/jobs.py`: archive_story(), archive_many(), resolve_story_id()
- `wattpad_crawler/models.py`: Story, Part, Comment, status Literals

**API Layer:**

- `wattpad_crawler/api/story.py`: fetch_story() + parse_story()
- `wattpad_crawler/api/user.py`: fetch_library(), fetch_list_story_ids()
- `wattpad_crawler/api/comments.py`: fetch_inline_comments(), fetch_end_comments()

**HTTP Client:**

- `wattpad_crawler/client.py`: RateLimitedClient, TokenBucket

**Archive Storage:**

- `wattpad_crawler/archive/state.py`: Manifest (SQLite backend)
- `wattpad_crawler/archive/store.py`: Atomic I/O, path layout

**Content Processing:**

- `wattpad_crawler/scrape/chapter_html.py`: extract_chapter() (BeautifulSoup)
- `wattpad_crawler/render/txt.py`: render_txt()
- `wattpad_crawler/render/html.py`: render_html()
- `wattpad_crawler/render/epub.py`: render_epub() (EbookLib)

**Web UI:**

- `wattpad_crawler/web/app.py`: build_app()
- `wattpad_crawler/web/routes.py`: All route handlers
- `wattpad_crawler/web/runner.py`: JobManager, JobRunner
- `wattpad_crawler/web/library_browser.py`: scan_library()
- `wattpad_crawler/web/templates/base.html`: Layout template
- `wattpad_crawler/web/templates/*.html`: Page templates

**Testing:**

- `tests/conftest.py`: Fixtures
- `tests/unit/test_*.py`: Unit tests (one per module)
- `tests/integration/test_end_to_end.py`: Full pipeline test
- `tests/fixtures/`: Mock API responses + sample HTML

## Naming Conventions

**Files:**

- Source modules: `snake_case.py` (e.g., `chapter_html.py`, `library_browser.py`)
- Test files: `test_<module>.py` matching source module name
- Package directories: `snake_case/` (no hyphens)
- Config: `_config.toml` (leading underscore for archive root)
- State database: `_state.sqlite` (leading underscore)

**Directories:**

- Archive structure: `stories/<author>/<story_id>_<slug>/`
  - author: sanitized username (alphanumeric + underscore/hyphen, max 80 chars)
  - story_id: numeric ID, safe-path-partified
  - slug: title slugified (lowercase, hyphen-joined, max 80 chars)
- Within story: `parts/` (chapter files), `output/` (rendered artifacts)
- Part file prefix: `{ordinal:02d}_{part_id}_{slug}` (e.g., `01_12345_chapter-one`)
  - Ordinal zero-padded to 2 digits
  - Part ID sanitized
  - Title slugified
  - Extensions: `.json`, `.html`, `.txt`, `-comments-inline.json`, `-comments-end.json`

**Variables & Functions:**

- Functions: `snake_case()` (e.g., `archive_story()`, `extract_chapter()`)
- Classes: `PascalCase` (e.g., Story, Manifest, RateLimitedClient)
- Enums/Literals: PascalCase for class names, lowercase for values (e.g., PartStatus = Literal["pending", "done"])
- Module-level constants: `UPPERCASE` (e.g., `STORY_FIELDS`, `STORY_URL`)
- Private functions: `_snake_case()` (e.g., `_safe_path_part()`, `_default_deps()`)
- Private methods: `_snake_case()` (e.g., `_run()`, `_mask()`)
- Test functions: `test_<scenario>` (e.g., `test_parser_has_expected_subcommands()`)

## Where to Add New Code

**New Feature (Archive Enhancement):**

- Core logic: `wattpad_crawler/jobs.py` — modify `archive_story()` or add new function
- API call: `wattpad_crawler/api/*.py` — add new endpoint fetcher
- Storage: `wattpad_crawler/archive/store.py` — add write function if storing new files
- State tracking: `wattpad_crawler/archive/state.py` — add Manifest CRUD method if tracking new data
- Tests: `tests/unit/test_jobs.py`, `tests/unit/test_api_*.py`, `tests/unit/test_store.py`

**New Web Route:**

- Handler: `wattpad_crawler/web/routes.py` — add @router.get/post route
- Template: `wattpad_crawler/web/templates/new_page.html`
- CSS: `wattpad_crawler/web/static/style.css` (add new selectors)
- Tests: `tests/unit/test_web_routes.py` — add test for route

**New CLI Subcommand:**

- Parser: `wattpad_crawler/cli.py` — add subparser to `build_parser()`
- Handler: `wattpad_crawler/cli.py:main()` — add elif branch in argument dispatch
- Tests: `tests/unit/test_cli.py` — test parser + handler

**New Data Model:**

- Definition: `wattpad_crawler/models.py` — add @dataclass or Literal type
- API parsing: `wattpad_crawler/api/*.py` — parse API response into model
- Storage: `wattpad_crawler/archive/store.py` — serialize to JSON if persisting

**New Render Format:**

- Renderer: `wattpad_crawler/render/format_name.py` — implement `render_<format>()` function
- Pipeline: `wattpad_crawler/jobs.py` — add to render loop in `archive_story()`
- Tests: `tests/unit/test_render_<format>.py`

**Testing:**

- Unit tests live in `tests/unit/test_<module>.py`
- Fixtures (mocks, temp data) in `tests/fixtures/`
- Use conftest.py fixtures: `fixtures_dir`, `output_dir`
- Mark slow tests with `@pytest.mark.live` (skipped by default)

## Special Directories

**wattpad-archive/ (Runtime Archive):**

- Purpose: Default local archive (user can override with --output)
- Generated: Yes (created by first `wattpad-crawler` run)
- Committed: No (in .gitignore)
- Ownership: User; should never be modified by git operations
- Safe to delete: Yes (recreated on next archive run, but data lost)
- SQLite database: `_state.sqlite` with WAL mode (reader-writer safe)

**wattpad_crawler/web/templates/ & static/:**

- Purpose: Jinja2 templates + CSS for web UI
- Generated: No (hand-written)
- Committed: Yes
- Included in wheel: Yes (via hatch.build.targets.wheel.force-include in pyproject.toml)
- Note: Must be bundled with package for installed CLI to serve UI

**.pytest_cache/, .ruff_cache/, wattpad_crawler/__pycache__/:**

- Purpose: Caches from tools (pytest, ruff, Python bytecode)
- Generated: Yes (auto-created on first run)
- Committed: No (in .gitignore)
- Safe to delete: Yes (rebuilt on next run)

---

*Structure analysis: 2026-05-03*
