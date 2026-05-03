# Coding Conventions

**Analysis Date:** 2026-05-03

## Naming Patterns

**Files:**
- Lowercase with underscores: `client.py`, `chapter_html.py`, `api_comments.py`
- Test files: `test_<module>.py` (e.g., `test_client.py`, `test_config.py`)
- Web route files: `routes.py`, `app.py` (per-module organization)

**Functions:**
- Lowercase with underscores: `build_client()`, `fetch_story()`, `parse_story()`
- Private functions: Leading underscore `_default_deps()`, `_tmp_path()`, `_safe_path_part()`
- Predicates: Prefixed with `is_` or use clear verb forms: `is_disconnected()` (from Starlette)

**Variables:**
- Lowercase with underscores: `story_id`, `output_dir`, `rate_limit_per_sec`
- Type aliases and status literals: `PartStatus`, `StoryStatus` (Literal types)
- Constants: Uppercase with underscores: `STORY_FIELDS`, `STORY_URL`
- Single-letter variables only in loops/iterables: `for i, p in enumerate(...)`

**Types:**
- Dataclasses use CapitalCase: `Config`, `Story`, `Part`, `Comment`
- Custom exception classes use CapitalCase: `ConfigError`, `ResolveError`
- Literal type aliases use CapitalCase: `PartStatus`, `StoryStatus`

## Code Style

**Formatting:**
- Tool: `ruff` (not black - ruff does formatting via `ruff format`)
- Line length: 100 characters (configured in `pyproject.toml`)
- Python version target: 3.11+

**Linting:**
- Tool: `ruff` with `ruff check`
- Rules selected: `["E", "F", "I", "UP", "W"]`
  - E: pycodestyle errors
  - F: Pyflakes (undefined names, unused imports)
  - I: isort (import sorting)
  - UP: pyupgrade (Python version upgrades)
  - W: pycodestyle warnings

**Import organization:**
- Order: Standard library → Third-party → First-party (configured in `pyproject.toml`)
- First-party known modules: `wattpad_crawler`
- Grouped logically with blank lines between groups
- Imports at module level, not inside functions (except async context manager imports in `web/routes.py`)

**Example from `wattpad_crawler/jobs.py`:**
```python
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from wattpad_crawler.api import comments as api_comments
from wattpad_crawler.api import story as api_story
from wattpad_crawler.archive import store
from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.config import Config
from wattpad_crawler.models import Story
from wattpad_crawler.render import epub as render_epub
from wattpad_crawler.render import html as render_html
from wattpad_crawler.render import txt as render_txt
from wattpad_crawler.scrape.chapter_html import ChapterContent, extract_chapter
```

## Type Hints

**Style:**
- Comprehensive type annotations on all functions and dataclass fields
- Union types use pipe syntax: `Exception | None` (Python 3.10+ style, required for 3.11)
- Optional values expressed as `T | None` not `Optional[T]`
- Return types always specified

**Examples:**
```python
def build_client(cfg: Config) -> httpx.Client:
    """Function with type hints."""
    pass

def _parse_retry_after(raw: str | None) -> float:
    """Optional parameters and returns."""
    pass

@dataclass(frozen=True)
class Config:
    output_dir: Path
    cookie: str = ""
    rate_limit_per_sec: float = 2.0
```

**Callable types:**
- Use `Callable[[ArgTypes], ReturnType]` from `collections.abc`
- See `JobDeps` dataclass in `wattpad_crawler/jobs.py` for dependency injection pattern

## Error Handling

**Custom Exceptions:**
- Define custom exception classes that inherit from `Exception`
- Example: `ConfigError` in `wattpad_crawler/config.py`, `ResolveError` in `wattpad_crawler/jobs.py`
- Custom exceptions used for domain-specific errors (config parsing, story resolution)

**Exception Patterns:**
```python
# Re-raise with explicit context
except tomllib.TOMLDecodeError as e:
    raise ConfigError(f"Invalid TOML in {config_path}: {e}") from e

# Log and continue (cover fetch failures)
except Exception as e:
    logger.warning("cover fetch failed: %s", e)
    return b""

# Log and mark as failed (part archive failures)
except Exception as e:
    logger.exception("part %s failed: %s", part.part_id, e)
    # Store error state in manifest
```

**Validation:**
- Eager validation at boundaries (config loading, API response parsing)
- Raise `ValueError` for invalid API responses with descriptive messages
- Validate preconditions in public methods with clear error messages

**Example from `wattpad_crawler/api/story.py`:**
```python
story_id_raw = raw.get("id")
if story_id_raw is None:
    raise ValueError(f"Story response missing 'id': keys={list(raw.keys())}")
```

## Logging

**Framework:** Standard library `logging` module

**Logger Creation:**
```python
logger = logging.getLogger(__name__)
```

**Logging Patterns:**
- Use `logger.info()` for significant events (archiving start, etc.)
- Use `logger.warning()` for recoverable failures (cover fetch failure, 429 rate limit)
- Use `logger.exception()` for unhandled exceptions (logs traceback automatically)

**Examples from `wattpad_crawler/client.py`:**
```python
logger.warning("429 on %s — sleeping %.1fs", url, wait)
logger.warning("Unparseable Retry-After header %r, defaulting to 60s", raw)
```

**Log Format (configured in CLI):**
- Format: `"%(asctime)s %(levelname)s %(name)s: %(message)s"`
- Set in `wattpad_crawler/cli.py` via `logging.basicConfig()`
- Verbosity controlled by `--verbose` flag (DEBUG vs INFO)

## Comments

**When to Comment:**
- Document non-obvious behavior: "Per-process, per-thread tmp filename — avoids collisions..."
- Explain architectural decisions: "Indirection layer so tests can inject fakes" (JobDeps dataclass)
- Add implementation notes: "We'd rather lose one weird character than fail to archive a comment"
- Link to specifications: "Spec note: ASCII-only slug is acceptable for v1; this test documents that"

**Docstring Style:**
- One-liner docstrings for simple functions
- Full docstrings for classes and complex methods
- Docstrings on public API only

**Example from `wattpad_crawler/archive/store.py`:**
```python
def _scrub_surrogates(s: str) -> str:
    """Replace lone UTF-16 surrogates with U+FFFD.

    Python `str` allows surrogate halves but `Path.write_text(..., encoding='utf-8')`
    rejects them. We'd rather lose one weird character than fail to archive
    a comment.
    """
```

## Async vs Sync

**Default:** Synchronous (blocking) code is the default throughout `wattpad_crawler/`

**Async used only in web layer:**
- `wattpad_crawler/web/routes.py`: FastAPI route handlers are async (required by FastAPI)
- Async patterns include form parsing: `form = await request.form()`
- Async request disconnection checks: `if await request.is_disconnected()`
- Thread-to-asyncio bridging: `await asyncio.sleep(0.25)` for polling integration

**Rationale:**
- Core crawling/archiving logic is I/O-bound but uses `httpx.Client` (synchronous httpx)
- Easier to reason about threading for rate limiting (TokenBucket uses `threading.Lock`)
- Web UI uses async for server efficiency but delegates to sync thread pools

**Example from `wattpad_crawler/web/routes.py`:**
```python
async def job_stream(request: Request, job_id: str, after: int = 0):
    async def event_gen():
        import asyncio
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.25)
```

## Context Managers and Cleanup

**Pattern:** Use context managers with `__enter__` / `__exit__` for resource cleanup

**Example from `wattpad_crawler/client.py`:**
```python
class RateLimitedClient:
    def __enter__(self) -> "RateLimitedClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
```

**Usage:**
```python
with RateLimitedClient(cfg) as client:
    # Use client
    pass  # automatically closed
```

## Dataclasses

**Style:**
- Use `@dataclass` decorator for simple data structures
- Use `frozen=True` for immutable configs: `@dataclass(frozen=True)`
- Frozen dataclasses prevent accidental mutation (tested in `test_config.py`)
- Field defaults via `default_factory` for mutable defaults

**Example from `wattpad_crawler/models.py`:**
```python
@dataclass
class Story:
    story_id: str
    title: str
    author_username: str
    description: str = ""
    cover_url: str = ""
    tags: list[str] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
```

## Testing Interfaces

**Dependency Injection Pattern:**
- Use dataclass wrappers for testable dependencies
- See `JobDeps` in `wattpad_crawler/jobs.py`
- Allows tests to inject mocks without monkeypatching
- Default implementation provided by `_default_deps()`

## Path Handling

**Style:**
- Use `pathlib.Path` everywhere (not `os.path`)
- Path operations on `Path` objects: `.read_text()`, `.write_text()`, `.exists()`, `.mkdir()`
- Type hints: `output_dir: Path`, `config_path: Path`

**Example:**
```python
output_dir.mkdir(parents=True, exist_ok=True)
config_path = output_dir / "_config.toml"
raw = config_path.read_text(encoding="utf-8")
```

---

*Convention analysis: 2026-05-03*
