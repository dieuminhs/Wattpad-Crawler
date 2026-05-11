---
phase: 01-local-hardening-fixes
plan: 02
subsystem: api/comments
tags: [recursion, comments, dos-mitigation, rel-01]
requirements: [REL-01]
dependency_graph:
  requires:
    - wattpad_crawler/models.py::Comment (existing dataclass — unchanged)
    - wattpad_crawler/client.py::RateLimitedClient (existing — unchanged)
  provides:
    - "wattpad_crawler.api.comments._MAX_COMMENT_DEPTH = 10 (module constant; REL-01)"
    - "wattpad_crawler.api.comments._parse_one(raw, depth=0, *, max_depth=_MAX_COMMENT_DEPTH) -> tuple[Comment | None, bool]"
    - "wattpad_crawler.api.comments.parse_comments_page emits one logger.warning per truncated top-level subtree"
  affects:
    - parse_comments_page callers (signature unchanged — only logging behavior added)
tech-stack:
  added: []
  patterns:
    - "Tuple return for truncation tracking (idiom matches existing parse_comments_page tuple return)"
    - "Module-level logger via logging.getLogger(__name__) — matches project convention"
    - "Lazy %-format args in logger.warning — matches client.py precedent"
key-files:
  created:
    - none
  modified:
    - wattpad_crawler/api/comments.py
    - tests/unit/test_api_comments.py
decisions:
  - "Default _MAX_COMMENT_DEPTH set as module constant (not Config-exposed) per D-11 — single user, no need to surface in TOML"
  - "Truncation warning fires in parse_comments_page (top-level loop), not _parse_one — avoids one-warning-per-recursive-call spam on adversarial deep chains"
  - "At depth >= max_depth the parent Comment is preserved with replies=[] (not None) — losing parent would be silent data loss (RESEARCH §Pitfall 4)"
  - "max_depth is keyword-only (after *,) so tests can override per-call without polluting the recursive depth positional"
metrics:
  tasks: 2
  files_changed: 2
  lines_added: 232
  lines_removed: 22
  tests_added: 10
  tests_passing: 11
  duration: ~10 min
  completed: 2026-05-03
---

# Phase 1 Plan 02: Bounded Comment-Reply Recursion Summary

Replaced unbounded recursion in `_parse_one()` with a depth-bounded variant capped at 10; added 10 unit tests proving the cap, the warning emission, and absence of `RecursionError` on a 30-level chain.

## What Changed

### `wattpad_crawler/api/comments.py`
- Added `import logging` and module-level `logger = logging.getLogger(__name__)`
- Added module constant `_MAX_COMMENT_DEPTH = 10` (REL-01 default)
- Refactored `_parse_one` signature from `(raw) -> Comment | None` to:

  ```python
  def _parse_one(
      raw: dict[str, Any],
      depth: int = 0,
      *,
      max_depth: int = _MAX_COMMENT_DEPTH,
  ) -> tuple[Comment | None, bool]:
  ```

- At `depth >= max_depth`, preserves the parent `Comment` with `replies=[]` and sets the truncation flag to `True` only if the raw payload had any replies (so leaves at the cap don't spuriously flag truncation).
- Aggregates `truncated` flag from descendants — any child returning `child_trunc=True` flips this subtree's flag.
- `parse_comments_page` signature unchanged (`(raw) -> tuple[list[Comment], str | None]`); now consumes the new `_parse_one` return shape and emits exactly one `logger.warning` per truncated top-level subtree using format string:

  ```
  "comment %s truncated: replies beyond depth %d dropped"
  ```

### `tests/unit/test_api_comments.py`
- Added `import logging` at top
- Added `_nest(level)` helper that constructs an N-level nested reply chain (root id `cN`, leaf id `c0`)
- Added 10 new test functions:

  | Test | Behavior |
  |------|----------|
  | `test_parse_one_caps_recursion_at_default_max_depth` | 15-level chain → 10 levels preserved + `truncated=True`; level-10 leaf has `replies=[]` |
  | `test_parse_one_respects_custom_max_depth` | `max_depth=3` honored when passed explicitly |
  | `test_parse_one_no_truncation_when_depth_below_cap` | 5-level chain under cap of 10 returns `truncated=False` and all 5 levels preserved |
  | `test_parse_one_no_recursion_error_on_30_level_chain` | 30-level chain at default cap exits cleanly |
  | `test_parse_one_returns_none_when_id_missing` | `(None, False)` on missing id |
  | `test_parse_one_skips_non_dict_replies` | strings/None in `replies` skipped, not crashed |
  | `test_parse_comments_page_logs_warning_on_truncation` | 15-level top-level → one `caplog` record on `wattpad_crawler.api.comments` mentioning `c15` and `truncat` |
  | `test_parse_comments_page_no_warning_when_under_cap` | 5-level top-level under cap → zero warnings |
  | `test_parse_comments_page_emits_one_warning_per_truncated_top_level` | Two top-level comments (one truncates, one doesn't) → exactly one warning |
  | `test_parse_one_monkeypatch_constant_changes_behavior` | Documents the contract that `_parse_one`'s default `max_depth` is captured at function-definition time; monkeypatching `_MAX_COMMENT_DEPTH` does NOT change it — tests must pass `max_depth=` explicitly |

## Test Results

```
tests/unit/test_api_comments.py ..........  11 passed in 0.07s
```

11 passed (1 pre-existing + 10 new). 30-level chain test completes in well under 1 second — recursion is bounded at depth 10 regardless of input depth.

## Verification Outputs

- `ruff check wattpad_crawler/api/comments.py tests/unit/test_api_comments.py` → All checks passed!
- `ruff format wattpad_crawler/api/comments.py` → 1 file left unchanged
- ROADMAP §Phase 1 success criterion #1 (the 15-level fixture script in the plan's `<verification>` block) printed `OK` and emitted exactly one warning line:

  ```
  WARNING:wattpad_crawler.api.comments:comment c15 truncated: replies beyond depth 10 dropped
  OK: 15-level chain parsed; depth-cap warning logged; top-level preserved with replies truncated at depth 10
  ```

## Must-Haves Verification

| Truth | Status | Evidence |
|-------|--------|----------|
| 15-level chain parsed without RecursionError via `_parse_one` | PASS | `test_parse_one_caps_recursion_at_default_max_depth` |
| `_parse_one(raw_15_level_chain, max_depth=10)` preserves top-level Comment with replies populated for first 10 levels and `replies=[]` at level-10 leaf | PASS | Same test walks 10 levels and asserts `cursor.replies == []` |
| `parse_comments_page` emits exactly one `logger.warning` per truncated top-level comment with id and truncation reference | PASS | `test_parse_comments_page_logs_warning_on_truncation` + `test_parse_comments_page_emits_one_warning_per_truncated_top_level` |
| `_parse_one` returns `tuple[Comment \| None, bool]` where bool is True if any reply at any depth was dropped | PASS | All recursion-cap tests destructure the tuple |
| Default `_MAX_COMMENT_DEPTH` is exactly 10 | PASS | Asserted in `test_parse_one_caps_recursion_at_default_max_depth` and acceptance smoke test |

## Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| `_MAX_COMMENT_DEPTH = 10` at module scope | PASS |
| `import logging` + `logger = logging.getLogger(__name__)` at module scope | PASS |
| `_parse_one` signature exact match | PASS |
| `if depth >= max_depth:` literal guard | PASS |
| `logger.warning(` inside `if was_truncated:` block | PASS |
| Smoke test `python -c "...assert _MAX_COMMENT_DEPTH == 10..."` exits 0 | PASS |
| `ruff check wattpad_crawler/api/comments.py` exits 0 | PASS |
| `_parse_one` referenced only in `comments.py` + `test_api_comments.py` (Grep verified) | PASS |
| All 10 new tests pass | PASS |
| 30-level chain test exits in under 1 second | PASS (entire suite 0.07s) |

## Deviations from Plan

None — plan executed exactly as written. The provided code blocks transcribed verbatim; ruff formatter left both files unchanged.

## Notes for Future Contributors

**Monkeypatch contract (locked by test):** Python evaluates default-argument expressions at function-definition time. Monkeypatching `wattpad_crawler.api.comments._MAX_COMMENT_DEPTH` does NOT change the default `max_depth` of an already-imported `_parse_one`. Tests that need a smaller cap must pass `max_depth=` explicitly. `test_parse_one_monkeypatch_constant_changes_behavior` documents this so a future contributor doesn't add a monkeypatch-based test that silently uses the original cap of 10.

**Why warning fires in `parse_comments_page` not `_parse_one`:** An adversarial 100-level reply chain would trigger N recursive calls at the cap level if the warning fired inside `_parse_one`. Aggregating the truncation flag and emitting one warning per top-level subtree keeps the log signal loud (visible) but quiet (not spammy) — matching D-18.

## Commits

- `779b270` — feat(01-02): cap comment recursion at depth 10 with truncation flag
- `b8a93fc` — test(01-02): cover REL-01 recursion cap in test_api_comments

## Self-Check: PASSED

- FOUND: wattpad_crawler/api/comments.py (modified)
- FOUND: tests/unit/test_api_comments.py (modified)
- FOUND: commit 779b270 (Task 1 — feat)
- FOUND: commit b8a93fc (Task 2 — test)
- FOUND: 11/11 tests passing
- FOUND: ruff check clean on both files
