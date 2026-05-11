# Testing Patterns

**Analysis Date:** 2026-05-03

## Test Framework

**Runner:**
- pytest 8.0+
- Config: `pyproject.toml` `[tool.pytest.ini_options]`

**Assertion Library:**
- Standard pytest assertions (`assert`)

**Run Commands:**
```bash
pytest                          # Run all non-live tests (default)
pytest -m live                  # Run only live API tests (hits real Wattpad API)
pytest tests/unit               # Run unit tests only
pytest tests/integration        # Run integration tests only
pytest -v                       # Verbose output
pytest --cov                    # Coverage report (if coverage plugin installed)
```

**Pytest Configuration (`pyproject.toml`):**
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: hits live Wattpad API; skipped by default"]
addopts = "-m 'not live'"
```

**Key behavior:**
- Default runs exclude `@pytest.mark.live` tests (added via `addopts`)
- Live tests require explicit opt-in with `-m live` (safety guard for CI/local runs)

## Test File Organization

**Directory Structure:**
```
tests/
├── conftest.py              # Shared fixtures
├── fixtures/                # Test data files
│   ├── api_responses/       # Mock API JSON responses
│   └── html_chapters/       # HTML chapter examples
├── unit/                    # Unit tests (no external dependencies)
│   ├── test_api_*.py        # API parser tests
│   ├── test_client.py       # HTTP client tests
│   ├── test_config.py       # Config loading tests
│   ├── test_jobs.py         # Archiving job tests
│   ├── test_*.py            # Other module tests
│   └── __init__.py
└── integration/             # Integration tests (may hit external services)
    ├── test_end_to_end.py   # Full archive workflow test (VCR cassette-based)
    ├── cassettes/           # VCR cassettes (recorded HTTP interactions)
    └── __init__.py
```

**Test File Naming:**
- Pattern: `test_<module>.py`
- Examples: `test_client.py`, `test_config.py`, `test_api_story.py`
- One test file per source module (mirrors `wattpad_crawler/` structure)

## Test Structure

**Suite Organization:**
- Flat organization: no nested classes, functions only
- One test function per behavior
- Test names describe the behavior: `test_client_sets_user_agent`, `test_slugify_basic`

**Example from `tests/unit/test_client.py`:**
```python
def test_client_sets_user_agent(tmp_path: Path):
    cfg = Config(output_dir=tmp_path, cookie="abc", user_agent="ua/1")
    client = build_client(cfg)
    try:
        assert client.headers["User-Agent"] == "ua/1"
    finally:
        client.close()
```

**Setup/Teardown:**
- Setup: Via pytest fixtures (preferred) or inline in test function
- Teardown: Via `try/finally` blocks or context managers
- Fixtures: Defined in `tests/conftest.py` and used as function arguments

**Assertion Style:**
- Direct `assert` statements (not `self.assertEqual`, etc.)
- Meaningful assertion messages optional (pytest shows values on failure)

## Shared Fixtures

**Location:** `tests/conftest.py`

**Available fixtures:**

```python
@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures directory for accessing test data files."""
    return Path(__file__).parent / "fixtures"

@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Isolated temporary directory for each test (wattpad-archive tree)."""
    out = tmp_path / "wattpad-archive"
    out.mkdir()
    return out
```

**Usage Pattern:**
```python
def test_load_config_creates_default_when_missing(output_dir: Path):
    """output_dir is a clean, isolated tmp directory for this test only."""
    cfg = load_config(output_dir)
    assert (output_dir / "_config.toml").exists()

def test_parse_story_basic(fixtures_dir: Path):
    """fixtures_dir points to tests/fixtures/ for reading test data."""
    raw = json.loads((fixtures_dir / "api_responses/story_metadata.json").read_text())
    s = parse_story(raw)
    assert s.story_id == "123456789"
```

## Mocking

**Framework:** `unittest.mock` (standard library)

**Patterns:**

1. **Direct MagicMock creation:**
```python
from unittest.mock import MagicMock

fake_client = MagicMock()
deps.fetch_chapter_html.assert_called_once()
```

2. **Dependency injection via dataclass (preferred for integration):**
```python
deps = JobDeps(
    fetch_story=MagicMock(return_value=story),
    fetch_chapter_html=MagicMock(return_value="<pre>body</pre>"),
    parse_chapter=MagicMock(return_value=ChapterContent(...)),
    fetch_inline_comments=MagicMock(return_value=[]),
    fetch_end_comments=MagicMock(return_value=[]),
    fetch_cover_bytes=MagicMock(return_value=b""),
)
archive_story(cfg, fake_client, manifest, "42", deps=deps)
```

3. **pytest.mark.monkeypatch for module-level substitution:**
```python
def test_post_jobs_story_creates_and_starts(output_dir: Path, monkeypatch):
    def fake_archive_story(cfg_arg, _client, _manifest, sid, *, deps=None, progress=None):
        captured["sid"] = sid
        if progress:
            progress("story.start", {"story_id": sid})

    monkeypatch.setattr("wattpad_crawler.web.routes.archive_story", fake_archive_story)
```

**What to Mock:**
- External HTTP clients: `RateLimitedClient`, `httpx.Client`
- API calls: `fetch_story()`, `fetch_chapter_html()`
- File I/O in tests that verify logic, not file operations
- Parser functions when testing orchestration logic

**What NOT to Mock:**
- Config loading (use real filesystem with `output_dir` fixture)
- Data models/dataclasses (use real instances)
- Storage functions like `atomic_write_text()` (test with real temp files)
- Internal helpers like `slugify()` (test their behavior directly)

## Test Data and Fixtures

**Fixture Files Location:** `tests/fixtures/`

**API Response Fixtures:**
- Location: `tests/fixtures/api_responses/`
- Files: JSON dumps of actual Wattpad API responses
- Example: `story_metadata.json` (used in `test_api_story.py`)
- Pattern: Tests load and parse these fixtures to verify parser correctness

**HTML Chapter Fixtures:**
- Location: `tests/fixtures/html_chapters/`
- Files: Sample chapter HTML pages
- Example: `chapter_with_images.html`
- Pattern: Tests parse these to verify chapter extraction

**Test Data Creation:**
```python
def test_load_config_reads_existing(output_dir: Path):
    # Write test TOML inline
    (output_dir / "_config.toml").write_text(
        'cookie = "abc123"\n'
        "rate_limit_per_sec = 0.5\n"
        "workers_per_story = 5\n"
    )
    cfg = load_config(output_dir)
    assert cfg.cookie == "abc123"
```

## Test Coverage

**Requirements:** None enforced (no minimum target configured)

**Coverage is not currently enabled** (pytest-cov not in dependencies)

**To add coverage testing:**
```bash
# Add to [project.optional-dependencies] dev in pyproject.toml:
pytest-cov
```

## Test Types

**Unit Tests (tests/unit/):**
- Scope: Single function or class method in isolation
- External dependencies: Mocked
- Examples:
  - `test_client.py`: TokenBucket rate limiting, client header setup
  - `test_config.py`: TOML parsing, validation logic
  - `test_api_*.py`: API response parsing
  - `test_store.py`: File writing, path slugification
  - `test_jobs.py`: Job orchestration with mock deps
- Count: ~18 test modules covering 90% of codebase

**Integration Tests (tests/integration/):**
- Scope: Full archiving workflow with real HTTP interactions
- Dependencies: Recorded via VCR cassettes (no real API calls needed)
- Example: `test_end_to_end.py` — fetches, parses, writes real story archive
- VCR cassettes: `tests/integration/cassettes/` (YAML format)

**VCR (Video Cassette Recorder) Pattern:**
- Records HTTP interactions on first run with `--record-mode=once`
- Replays recorded interactions on subsequent runs
- Cassettes are committed to git (immutable test data)
- Safety: Cassettes reviewed before commit to prevent credential leaks

**Current Status:**
- End-to-end test disabled with `@pytest.mark.skip`
- Requires manual cassette recording (documented in test file)
- Instructions in `tests/integration/test_end_to_end.py`

## Test Patterns by Feature

**Testing HTTP Client Behavior:**
```python
# Timeout and error handling
def test_token_bucket_blocks_when_empty():
    bucket = TokenBucket(rate_per_sec=10.0, capacity=2)
    bucket.take()
    bucket.take()
    start = time.monotonic()
    bucket.take()  # should sleep ~0.1s
    elapsed = time.monotonic() - start
    assert 0.05 < elapsed < 0.3
```

**Testing Config Loading and Validation:**
```python
def test_load_config_rejects_bad_toml(output_dir: Path):
    (output_dir / "_config.toml").write_text("not a [valid toml")
    with pytest.raises(ConfigError):
        load_config(output_dir)

def test_config_is_frozen():
    """Frozen dataclass prevents mutation."""
    cfg = Config(output_dir=_P("/tmp"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.cookie = "mutated"
```

**Testing Data Models:**
```python
def test_parse_story_handles_null_tags():
    raw = {"id": "1", "title": "T", "user": {"name": "a"}, "tags": None, "parts": []}
    s = parse_story(raw)
    assert s.tags == []

def test_parse_story_raises_on_missing_id():
    with pytest.raises(ValueError, match="missing 'id'"):
        parse_story({"title": "T", "user": {"name": "a"}})
```

**Testing Job Workflows:**
```python
def test_archive_story_writes_all_artifacts(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    deps = _make_deps(story)  # Fixture that returns MagicMocks
    archive_story(cfg, fake_client, manifest, "42", deps=deps)
    
    sd = output_dir / "stories" / "bob" / "42_hi"
    assert (sd / "metadata.json").exists()
    row = manifest.get_part("42", "100")
    assert row["status"] == "done"
    manifest.close()
```

**Testing Threading/Concurrency:**
```python
def test_job_emit_is_thread_safe():
    """Concurrent emits from multiple threads must not lose events."""
    job = Job(job_id="j1", kind="archive_story", args={})

    def worker(n: int):
        for i in range(50):
            job.emit("tick", {"n": n, "i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert len(job.events) == 200
```

**Testing Web Routes (FastAPI + TestClient):**
```python
from fastapi.testclient import TestClient
from wattpad_crawler.web.app import build_app

def test_setup_page_renders(output_dir: Path):
    cfg = Config(output_dir=output_dir)
    app = build_app(cfg)
    client = TestClient(app)
    r = client.get("/setup")
    assert r.status_code == 200
    assert "cookie" in r.text.lower()
```

## CI/Test Configuration

**Automated Test Runs:**
- Not explicitly configured in this repo yet
- Ready for integration with GitHub Actions / GitLab CI
- Run command: `pytest` (or `pytest -m live` to include live tests)

**Pre-commit Hooks:**
- Not configured (no `.pre-commit-config.yaml` found)
- Could add: linting via ruff, type checking via mypy

---

*Testing analysis: 2026-05-03*
