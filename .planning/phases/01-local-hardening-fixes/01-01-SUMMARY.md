---
phase: 01-local-hardening-fixes
plan: 01
subsystem: security
tags: [sanitization, html, nh3, xss, scrape, allowlist]

# Dependency graph
requires:
  - phase: 00-bootstrap
    provides: existing extract_chapter() pipeline + BeautifulSoup paragraph extraction
provides:
  - "Module-scope nh3.Cleaner with reading-rich allowlist (D-01..D-04)"
  - "Sanitized paragraphs[i]['html'] field on every ChapterContent"
  - "nh3 0.3.5 pinned as runtime dep (nh3>=0.3,<0.4)"
  - "12 unit tests validating XSS/href/data-attr/style allowlist behavior"
affects: [render, epub, html, txt, web/reader, future comment sanitization]

# Tech tracking
tech-stack:
  added: ["nh3 0.3.5 (Rust-backed Ammonia HTML sanitizer)"]
  patterns:
    - "Module-scope nh3.Cleaner constructed once at import; per-paragraph .clean() inside extract loop"
    - "Explicit attributes={'*': {'data-p-id'}} (NOT generic_attribute_prefixes — narrow surface)"
    - "Default url_schemes preserved (excludes javascript:/data: out-of-the-box)"
    - "Default link_rel='noopener noreferrer' preserved (defense-in-depth)"

key-files:
  created:
    - ".planning/phases/01-local-hardening-fixes/01-01-SUMMARY.md"
  modified:
    - "pyproject.toml (added nh3>=0.3,<0.4 dependency)"
    - "local_story_archive/scrape/chapter_html.py (added _PARAGRAPH_CLEANER + per-paragraph .clean call)"
    - "tests/unit/test_chapter_html.py (added 12 sanitization tests)"

key-decisions:
  - "Used nh3.Cleaner instance (not nh3.clean function) so allowlist config is constructed once at module import"
  - "Allowlist is reading-rich (img/br/b/i/em/strong/u/a) per D-01 — bold/italic markup matters for archived reading experience"
  - "Per-tag attrs: img[src,alt], a[href], *[data-p-id] per D-02 — narrowest surface that supports the existing pipeline"
  - "Class and style attributes stripped from every tag per D-03 — renderers use project CSS anyway"
  - "Sanitization runs INSIDE extract_chapter (D-04) so stored JSON is already clean; renderers consume pre-sanitized data"
  - "Did NOT pass url_schemes kwarg — nh3 default already excludes javascript:/data:"
  - "Did NOT pass link_rel=None — kept nh3 default 'noopener noreferrer' for defense in depth"

patterns-established:
  - "Sanitize-at-store-boundary: untrusted HTML is cleaned the moment it crosses into our storage layer; downstream consumers (render, web reader) trust the stored data"
  - "Module-scope sanitizer instance: construct heavy objects once at import, reuse for the process lifetime (RESEARCH §Pattern 1)"

requirements-completed: [SAN-01, SAN-02]

# Metrics
duration: ~3 min (commit timestamps: 13:06:57 -> 13:09:30 +07:00)
completed: 2026-05-03
---

# Phase 1 Plan 1: HTML Sanitization Summary

**Module-scope nh3.Cleaner sanitizes paragraph HTML at extract-time with reading-rich allowlist (img/br/b/i/em/strong/u/a + data-p-id), closing the stored-XSS path before EPUB/HTML render layer.**

## Performance

- **Duration:** ~3 min (per-task commit span: 2026-05-03T13:06:57 -> 13:09:30 +07:00)
- **Started:** 2026-05-03T13:06:57+07:00
- **Completed:** 2026-05-03T13:09:30+07:00
- **Tasks:** 3 of 3
- **Files modified:** 3 (pyproject.toml, local_story_archive/scrape/chapter_html.py, tests/unit/test_chapter_html.py)

## Accomplishments

- nh3 0.3.5 pinned as runtime dependency (`nh3>=0.3,<0.4`); bleach explicitly NOT introduced (SAN-02 satisfied)
- `_PARAGRAPH_CLEANER` constructed exactly once at module scope in `chapter_html.py` with the locked D-01..D-03 allowlist config
- `extract_chapter()` now sanitizes every `paragraphs[i]["html"]` value via `_PARAGRAPH_CLEANER.clean(raw_html)` before storage (SAN-01, D-04 satisfied)
- 12 new sanitization unit tests added covering: script-stripping, onerror-stripping, data-p-id preservation, javascript:/http:/https: href handling, reading-rich tag preservation, br+img preservation, class/style stripping, disallowed-tag stripping, narrow data-* allowlist, html-field-shape smoke
- All 19 chapter_html tests pass (7 pre-existing + 12 new); ruff check clean on both modified source files
- Phase 1 ROADMAP success criterion #2 satisfied: chapter with `<img src=...>`, `<br>`, and `data-p-id` extracts cleanly with all three intact in `paragraphs[i]["html"]`

## Exact Cleaner Config Used

```python
_PARAGRAPH_CLEANER = nh3.Cleaner(
    tags={"img", "br", "b", "i", "em", "strong", "u", "a"},
    attributes={
        "img": {"src", "alt"},
        "a": {"href"},
        "*": {"data-p-id"},
    },
    strip_comments=True,
)
```

(Defaults left untouched: `url_schemes` already excludes `javascript:`/`data:`; `link_rel="noopener noreferrer"` retained as defense in depth.)

## Task Commits

Each task was committed atomically (with `--no-verify` per parallel-execution protocol):

1. **Task 1: Add nh3 0.3.x runtime dependency to pyproject.toml** — `2a70bf1` (chore)
2. **Task 2: Add module-scope `_PARAGRAPH_CLEANER` and sanitize inside `extract_chapter`** — `d408593` (feat)
3. **Task 3: Write 12 sanitization unit tests** — `ce4cefa` (test)

_Note: Task 2 was marked tdd="true" in the plan, but the plan structures the implementation in Task 2 and the formal test suite in Task 3 — the TDD intent is satisfied by Task 3 acting as the verification suite for Task 2's behavior contract. A smoke-script in Task 2's verification block confirmed the implementation before commit; the full suite in Task 3 then locked in all D-01..D-04 invariants._

## Files Created/Modified

- `pyproject.toml` — added `"nh3>=0.3,<0.4",` to `[project] dependencies` (1 line); dev dependencies block untouched
- `local_story_archive/scrape/chapter_html.py` — added `import nh3`, `_PARAGRAPH_CLEANER = nh3.Cleaner(...)` module-scope constant, and `clean_html = _PARAGRAPH_CLEANER.clean(raw_html)` call inside the `for para in para_els:` loop; ruff format applied
- `tests/unit/test_chapter_html.py` — appended 12 new test functions covering the full D-01..D-04 surface; existing 7 tests untouched
- `.planning/phases/01-local-hardening-fixes/01-01-SUMMARY.md` — this file

## Decisions Made

All decisions were locked upstream in `01-CONTEXT.md` (D-01 through D-04). No new decisions were taken at execution time:

- D-01 reading-rich allowlist: implemented exactly as specified
- D-02 per-tag attrs + universal `data-p-id` via `attributes={"*": {"data-p-id"}}`: implemented; explicitly NOT used `generic_attribute_prefixes` (would widen the data-* surface)
- D-03 strip class/style from every tag: implemented (no allowlist entry exists for `class` or `style`)
- D-04 sanitize inside `extract_chapter()`: implemented at the documented call site (per-paragraph, before storage)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test 3 fixture used `<span>` which isn't in the allowlist**
- **Found during:** Task 3 (sanitization unit tests — 1 of 12 failed on first run)
- **Issue:** The plan's literal fixture for `test_extract_chapter_preserves_data_p_id_on_inner_elements` was `<span data-p-id="inner">child</span>`. Since `<span>` is not in the D-01 tag allowlist, nh3 strips the entire `<span>` element (preserving only its text content). This drops `data-p-id` along with the tag, so the assertion `'data-p-id="inner"' in result.paragraphs[0]["html"]` could never pass. The plan's verification block in `<verification>` had the same bug.
- **Fix:** Rewrote the fixture to use `<b data-p-id="inner">child</b>` (an allowed tag from D-01). The function name `test_extract_chapter_preserves_data_p_id_on_inner_elements` is preserved exactly (per Task 3 acceptance criterion). The intent — "data-p-id is universally allowed via `attributes={'*': {'data-p-id'}}`" — is unchanged and now correctly observable. Added an in-test comment explaining the rewrite.
- **Files modified:** `tests/unit/test_chapter_html.py`
- **Verification:** All 19 tests pass on re-run (7 pre-existing + 12 new). The narrow-data-attr test (`test_extract_chapter_strips_data_attributes_other_than_p_id`) independently validates the D-02 universal allowlist by using `<img>` with `data-p-id` and `data-tracking`, confirming only `data-p-id` survives.
- **Committed in:** `ce4cefa` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug in plan's test fixture)
**Impact on plan:** Test correctness only. The plan's behavioral intent (D-02 universal data-p-id allowlist) is preserved and now properly tested. No security/correctness deviation in the implementation itself.

## Issues Encountered

- The worktree does not contain its own `.venv`; it shares the parent project's `.venv` at `D:\Dev\Local Story Archive\.venv`. Used `D:/Dev/Local Story Archive/.venv/Scripts/python.exe` directly for all installs/tests/lints. No GSD-tools issue — just an environment note for future worktree-based plans in this repo.
- pytest-vcr is the configured VCR plugin (`pytest-vcr 1.0.2` per pyproject dev deps); no live HTTP was hit by these unit tests, so no cassette interaction occurred.

## Threat Flags

No new security-relevant surface introduced beyond what was already scoped in the plan's `<threat_model>`. The implementation satisfies all `mitigate` dispositions:

- **T-01-01** (stored XSS): mitigated via `_PARAGRAPH_CLEANER` allowlist + per-paragraph `.clean()` call
- **T-01-02** (active-content link rel): mitigated via nh3 default `link_rel="noopener noreferrer"` (left in place)
- **T-01-03** (data-* over-broad allowlist): mitigated via explicit `attributes={"*": {"data-p-id"}}` (NOT `generic_attribute_prefixes`); regression-tested by `test_extract_chapter_strips_data_attributes_other_than_p_id`
- **T-01-04** (oversized HTML OOM): accepted out of scope per threat register

## Known Stubs

None. The implementation is complete and wired into the live `extract_chapter` path; no placeholder data, no TODO markers, no UI stubs.

## User Setup Required

None — no external service configuration required. nh3 is a pure Python wheel install and is fetched automatically via `pip install -e .` (or already-present after this plan).

## Next Phase Readiness

- SAN-01 + SAN-02 closed; downstream Plan 01-02..05 work can proceed in parallel (they touch different modules: comments recursion, runner caps, render error path)
- The `paragraphs[i]["html"]` field is now safe-by-default for all renderers (txt/html/epub) and the web reader. Future renderer hardening (e.g., per-format allowlist tweaks) is deferred per `01-CONTEXT.md` deferred-ideas section.
- The shared rate-limited venv install pattern works for parallel worktree agents — no install conflicts observed.

## Self-Check: PASSED

Verified before write-out:

- File `pyproject.toml` exists and contains `nh3>=0.3,<0.4` (line 19)
- File `local_story_archive/scrape/chapter_html.py` exists and contains `_PARAGRAPH_CLEANER = nh3.Cleaner(`, `_PARAGRAPH_CLEANER.clean(raw_html)`, `"a": {"href"}`, `"*": {"data-p-id"}`; does NOT contain `generic_attribute_prefixes` or `url_schemes=`
- File `tests/unit/test_chapter_html.py` exists and contains all 12 required test function definitions
- Commits `2a70bf1`, `d408593`, `ce4cefa` exist on the worktree branch (verified via `git log 314bae4..HEAD`)
- All 19 tests pass; ruff check clean on both modified source files; `python -c "import nh3; assert hasattr(nh3, 'Cleaner')"` exits 0
- Phase 1 ROADMAP success criterion #2 verified end-to-end (img + br + data-p-id all intact post-sanitization)

---
*Phase: 01-local-hardening-fixes*
*Completed: 2026-05-03*
