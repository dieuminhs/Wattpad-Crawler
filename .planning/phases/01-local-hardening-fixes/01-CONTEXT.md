# Phase 1: Local hardening fixes - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Three classes of silent failure eliminated without touching the archive pipeline structure:

1. **Unbounded recursion** — comment-reply parsing is depth-capped (REL-01)
2. **Unbounded memory growth** — `Job.events` and `JobManager._jobs` are capped (REL-02, REL-03)
3. **Unsanitized HTML in stored archive** — `nh3` allowlist runs at extract-time (SAN-01, SAN-02)
4. **Silent render failures** — if all three renderers fail for a story, the job ends `failed` with a per-format breakdown (REL-04)

In scope: edits to `api/comments.py`, `web/runner.py`, `web/routes.py` (SSE param rename), `jobs.py` (RenderError + render loop), `scrape/chapter_html.py` (sanitization call site), `pyproject.toml` (nh3 dep).

Out of scope: pipeline restructuring, parallel chapter fetch (Phase 4), circuit-breakers (Phase 3), auth (Phase 2), comment-body sanitization, persisting jobs to SQLite, schema changes to `_state.sqlite`.

</domain>

<decisions>
## Implementation Decisions

### Sanitization (SAN-01, SAN-02)

- **D-01:** Allowlist is **reading-rich** — tags: `img`, `br`, `b`, `i`, `em`, `strong`, `u`, `a`. Strips bold/italic markup is **not** acceptable for archived reading experience.
- **D-02:** Per-tag attrs: `img[src, alt]`, `a[href]` (validated to `http://` or `https://` only — non-conforming URLs strip the attribute, leaving plain text). All elements may carry `data-p-id` (must be in nh3's `attributes` allowlist explicitly because nh3 strips `data-*` by default).
- **D-03:** `class` and `style` attributes are **stripped from every tag**. Renderers use the project's own CSS/EPUB stylesheet anyway. Smaller XSS surface.
- **D-04:** Sanitization runs **inside `extract_chapter()`** in `local_story_archive/scrape/chapter_html.py` — each `paragraphs[i]["html"]` field passes through `nh3.clean()` before being added to `ChapterContent`. Stored JSON is already clean; renderers consume pre-sanitized data.
- **D-05:** Comment `body` text is **not** sanitized in this phase — REQUIREMENTS.md SAN-01 names "paragraph HTML" specifically; comment-injection is deferred. Comments in EPUB output are escaped at render time (already true).

### Event-cap eviction (REL-02)

- **D-06:** `Job.events` becomes `collections.deque[ProgressEvent]` with `maxlen=_MAX_EVENTS_PER_JOB` (default 1000). Old events evict from the left automatically.
- **D-07:** Add monotonic `Job._next_seq: int` counter. `Job.emit()` assigns the next seq to each new `ProgressEvent` and increments the counter. `ProgressEvent` gains a `seq: int` field.
- **D-08:** `Job.snapshot_events(after_seq: int = 0)` replaces `snapshot_events(after_index: int = 0)` — returns events whose `seq > after_seq`. Indexes-into-list semantics are abandoned because eviction would shift them.
- **D-09:** SSE route in `web/routes.py` **renames `?after=N` → `?after_seq=N`** and the matching template/JS. Single-user, no API contract to preserve — clean break is acceptable.
- **D-10:** **Eviction-warning event:** if `after_seq` is older than the oldest seq still in the deque, emit a synthetic `events.evicted` event (with the dropped count) ahead of the snapshot so the UI can show "older events were dropped to save memory." Honest about the gap.

### Cap configurability (REL-01, REL-02, REL-03)

- **D-11:** All three caps are **module-level constants**, not Config-exposed:
  - `_MAX_COMMENT_DEPTH = 10` in `local_story_archive/api/comments.py`
  - `_MAX_EVENTS_PER_JOB = 1000` in `local_story_archive/web/runner.py`
  - `_MAX_JOBS = 50` in `local_story_archive/web/runner.py`
  Tests monkeypatch the constants for unit testing. No TOML plumbing because no realistic solo-user reason to tune these.
- **D-12:** `_parse_one(raw, depth=0, max_depth=_MAX_COMMENT_DEPTH)` — depth is a positional param so recursive calls can increment; `max_depth` is a keyword param defaulting to the module constant so tests can pass `max_depth=3` per call. Matches REQUIREMENTS.md REL-01 wording.
- **D-13:** **JobManager pruning preserves running jobs:** when `create()` would push count over `_MAX_JOBS`, iterate `_order` from oldest forward and evict only jobs with `status in {done, failed}`. Running jobs are pinned. Cap may be temporarily exceeded if many jobs are running simultaneously — that's better than orphaning a JobRunner thread that's still emitting events to a dropped Job.

### Render failure & comment truncation (REL-04 + REL-01)

- **D-14:** New `RenderError(Exception)` lives in `local_story_archive/jobs.py` alongside `ResolveError`. Single-purpose: signals "all renderers failed for this story."
- **D-15:** `archive_story()` render section restructures to:
  1. Build `render_status: dict[str, Literal["ok", "failed"]]` covering txt/html/epub
  2. For each renderer: try → mark `ok`; except → mark `failed`, emit `render.failed` (existing event)
  3. Emit `story.done` with `render_status` field included
  4. After the loop, if all three values are `"failed"`, raise `RenderError(f"all renders failed: {render_status}")`
- **D-16:** **Exception propagation:** JobRunner already catches `Exception` → `job.set_failed`, so `RenderError` flows through unchanged. `archive_many()` already catches per-story exceptions and records `failed: {e}` in the batch results dict — same path.
- **D-17:** **Comment truncation semantics:** at `depth >= max_depth`, the parent comment is preserved with `replies=[]`. Deeper-nested replies are dropped entirely (no synthetic placeholder). This preserves what we got at the cap level rather than discarding visible context.
- **D-18:** **Truncation warning frequency:** `_parse_one` tracks whether truncation occurred during its recursive descent (return value or thread-local counter — planner's choice). At each top-level comment, if its subtree was truncated, emit one `logger.warning` including the parent comment id and the dropped reply count. Loud enough to notice, quiet enough to not spam.

### Claude's Discretion

- **Exact nh3 API surface** — `nh3.clean()` vs. `nh3.Cleaner(...)` instance; researcher should check `nh3 0.3.x` docs for the most ergonomic shape and `data-p-id` allowlisting mechanism.
- **Truncation tracking mechanism** — return-tuple `(comment, was_truncated)` vs. mutable counter passed in vs. exception-based; planner picks whichever keeps `_parse_one` simplest.
- **Test fixture shapes** — synthetic 15-level reply chain (REL-01), JSON paragraph with `<script>`/`<img onerror=...>` (SAN-01), 1100-event Job (REL-02), 60-job submit (REL-03), 3-renderer-failure story (REL-04).
- **Eviction-warning event payload** — specifically what to put in `events.evicted` data dict (`dropped_count`, `oldest_seq_now`, etc.); planner chooses based on what the UI needs to display.

### Folded Todos

None — `gsd-tools todo match-phase 1` returned zero matches.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level

- `.planning/REQUIREMENTS.md` — Authoritative for v1 requirements REL-01..04, SAN-01..02; defaults (10 / 1000 / 50) and `nh3 0.3.x` are locked here, not negotiable in this phase
- `.planning/PROJECT.md` §Constraints — `nh3` chosen over `bleach`; backwards compat with existing `_state.sqlite`; single-user audience
- `.planning/ROADMAP.md` §"Phase 1: Local hardening fixes" — Goal statement and four success criteria; verifier will check these literally

### Codebase intel

- `.planning/codebase/CONCERNS.md` §"Comment recursion unbounded", §"Job events list grows unbounded per job", §"Web server holds unbounded job history in memory", §"Paragraph HTML stored without sanitization", §"Failed renders are silently skipped" — origin of all six requirements
- `.planning/codebase/CONVENTIONS.md` §"Naming Patterns", §"Type Hints", §"Error Handling" — leading underscore for private constants; pipe-syntax unions; custom exception classes inherit from `Exception`
- `.planning/codebase/STRUCTURE.md`, `.planning/codebase/ARCHITECTURE.md` — Layered architecture; `archive_story()` is the central pipeline that callers (CLI + JobRunner) wrap

### Files to edit (verified in scout)

- `local_story_archive/api/comments.py:12-33` — `_parse_one()` recursion site for REL-01
- `local_story_archive/web/runner.py:18-41` — `Job` dataclass, `events` field, `emit()` for REL-02
- `local_story_archive/web/runner.py:62-65` — `snapshot_events()` for SSE seq migration
- `local_story_archive/web/runner.py:72-94` — `JobManager` for REL-03
- `local_story_archive/web/routes.py` — SSE handler that consumes `?after=N` for D-09 rename
- `local_story_archive/jobs.py:131-142` — render loop for REL-04
- `local_story_archive/jobs.py:146-147` — `ResolveError` defined here; `RenderError` joins it
- `local_story_archive/scrape/chapter_html.py:35-45` — paragraph extraction site for SAN-01
- `pyproject.toml` lines 10-19 — dependencies block for SAN-02 (`nh3>=0.3,<0.4`)

### External (researcher to fetch)

- nh3 0.3.x documentation — `clean()` signature, default tag/attribute allowlists, mechanism for allowing `data-p-id` (which `nh3` strips by default along with all `data-*`), URL-scheme validation for `<a href>`. (No local URL — fetch from PyPI/GitHub at research time.)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`logger = logging.getLogger(__name__)` pattern** — every module already has one; reuse for the depth-truncation warning and any new diagnostic.
- **`@dataclass` with `frozen=True` for value types** — `ProgressEvent` is currently mutable; adding a `seq` field doesn't require freezing it. Match the existing style.
- **`StrEnum` for `JobStatus`** — already used for status comparison; `status in {done, failed}` for prune predicate is idiomatic.
- **`threading.Lock` per Job** — existing `Job._lock` already serializes `events.append`. Switching to `deque(maxlen=...)` keeps the same thread-safety story (deque appends are atomic in CPython).
- **`ResolveError` as Exception subclass** — pattern to copy verbatim for `RenderError`.

### Established Patterns

- **Custom exception classes inherit from `Exception` (not domain bases)** — keeps imports flat. `RenderError(Exception)` matches.
- **Tests inject deps via `JobDeps`** — the render section in `archive_story` doesn't go through `JobDeps`; renderers are imported directly. Phase 1 doesn't have to refactor this, but unit-testing render-failure-of-all-three may need pytest's `monkeypatch` of the renderer functions instead of `JobDeps`.
- **`logger.warning(...)` for recoverable issues, `logger.exception(...)` for caught exceptions** — use `warning` for the depth-truncation log; the render-failure path already uses `exception`.

### Integration Points

- **SSE route** (`web/routes.py`) — already polls `Job.snapshot_events(after_index)` every 0.25s; switching the param name is a one-line change, but the corresponding JS template needs to track `after_seq` instead of `after`. Plan must include the template edit.
- **`Job.emit` is called from many sites** — the `seq` field is added inside `emit`; callers don't change.
- **`archive_many` calls `archive_story`** — when `archive_story` raises `RenderError`, `archive_many` catches it and continues with the next story. Existing behavior is preserved.

</code_context>

<specifics>
## Specific Ideas

- **Reading-rich allowlist** is the user's preference: bold/italic/links matter for the archived reading experience. Spec-minimum (only `<img>`/`<br>`) was rejected as too aggressive even though it matches the literal letter of REQUIREMENTS.md SAN-01.
- **Honest eviction signaling** — when SSE clients miss events because of the cap, the UI should know. The synthetic `events.evicted` event is the chosen mechanism. Don't silently fast-forward.
- **Module constants over Config** — single-user tool; tuning these caps via TOML is overkill. The constants are easy to change in code if a real need ever appears, and tests can monkeypatch.
- **Running jobs are pinned** — pruning never evicts a job whose JobRunner thread is still active. Worst case: cap exceeded by N running jobs. Better than orphaning a writer.

</specifics>

<deferred>
## Deferred Ideas

- **Comment body sanitization** — defer until comment-injection is observed in the wild. Phase 1 stays focused on paragraph HTML per SAN-01.
- **`nh3` allowlist tuning per-format** — different allowlists for EPUB vs. HTML (e.g., EPUB readers may not handle `<a target>`) is theoretical; one allowlist for the stored data is sufficient.
- **Persist evicted events to disk** — out of scope; ephemeral memory cap is the chosen trade-off.
- **`max_jobs_in_memory` as a config field** — only matters if a future v2 introduces multi-user / longer-running web sessions.

</deferred>

---

*Phase: 01-local-hardening-fixes*
*Context gathered: 2026-05-03*
