# Phase 1: Local hardening fixes - Research

**Researched:** 2026-05-03
**Domain:** HTML sanitization (nh3), bounded recursion, bounded in-memory queues, render-failure error propagation
**Confidence:** HIGH

## Summary

Phase 1 lands six surgical hardening fixes: depth-cap on comment-reply recursion (REL-01), event-deque cap on `Job.events` (REL-02), pruning of `JobManager._jobs` (REL-03), `RenderError` when all three renderers fail (REL-04), `nh3` paragraph-HTML sanitization at extract-time (SAN-01), and `nh3` added to `pyproject.toml` (SAN-02). Every locked decision in CONTEXT.md is implementable with the existing `nh3 0.3.x` API surface and standard library primitives.

The single non-trivial external integration is `nh3`. Its 0.3.x API exposes both `nh3.clean(html, ...)` and the reusable `nh3.Cleaner(...)` instance — both accept identical kwargs. For Phase 1 the planner should use `nh3.Cleaner` constructed once at module scope in `wattpad_crawler/scrape/chapter_html.py` and reuse across the per-paragraph loop; this is the documented idiomatic pattern for fragment-by-fragment sanitization with a fixed allowlist. The required `data-p-id` attribute can be allowed cleanly through the `attributes={"*": {"data-p-id"}}` parameter (universal-tag allowlist), and `url_schemes` defaulting to ammonia's safe-scheme list (which includes `http` and `https`) handles D-02's URL validation without further configuration — disallowed schemes strip the attribute, preserving the link text.

**Primary recommendation:** Module-scope `nh3.Cleaner` instance in `chapter_html.py`, called inside the existing `for para in para_els:` loop on the `decode_contents()` output before assignment to `paragraphs[i]["html"]`. All six requirements are implementable as additive changes — no refactor of the archive pipeline required.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Sanitization (SAN-01, SAN-02):**
- **D-01:** Allowlist is reading-rich — tags: `img`, `br`, `b`, `i`, `em`, `strong`, `u`, `a`. Strips bold/italic markup is **not** acceptable for archived reading experience.
- **D-02:** Per-tag attrs: `img[src, alt]`, `a[href]` (validated to `http://` or `https://` only — non-conforming URLs strip the attribute, leaving plain text). All elements may carry `data-p-id` (must be in nh3's `attributes` allowlist explicitly because nh3 strips `data-*` by default).
- **D-03:** `class` and `style` attributes are **stripped from every tag**. Renderers use the project's own CSS/EPUB stylesheet anyway. Smaller XSS surface.
- **D-04:** Sanitization runs **inside `extract_chapter()`** in `wattpad_crawler/scrape/chapter_html.py` — each `paragraphs[i]["html"]` field passes through `nh3.clean()` before being added to `ChapterContent`. Stored JSON is already clean; renderers consume pre-sanitized data.
- **D-05:** Comment `body` text is **not** sanitized in this phase — REQUIREMENTS.md SAN-01 names "paragraph HTML" specifically; comment-injection is deferred. Comments in EPUB output are escaped at render time (already true).

**Event-cap eviction (REL-02):**
- **D-06:** `Job.events` becomes `collections.deque[ProgressEvent]` with `maxlen=_MAX_EVENTS_PER_JOB` (default 1000). Old events evict from the left automatically.
- **D-07:** Add monotonic `Job._next_seq: int` counter. `Job.emit()` assigns the next seq to each new `ProgressEvent` and increments the counter. `ProgressEvent` gains a `seq: int` field.
- **D-08:** `Job.snapshot_events(after_seq: int = 0)` replaces `snapshot_events(after_index: int = 0)` — returns events whose `seq > after_seq`. Indexes-into-list semantics are abandoned because eviction would shift them.
- **D-09:** SSE route in `web/routes.py` **renames `?after=N` → `?after_seq=N`** and the matching template/JS. Single-user, no API contract to preserve — clean break is acceptable.
- **D-10:** **Eviction-warning event:** if `after_seq` is older than the oldest seq still in the deque, emit a synthetic `events.evicted` event (with the dropped count) ahead of the snapshot so the UI can show "older events were dropped to save memory."

**Cap configurability (REL-01, REL-02, REL-03):**
- **D-11:** All three caps are **module-level constants**, not Config-exposed:
  - `_MAX_COMMENT_DEPTH = 10` in `wattpad_crawler/api/comments.py`
  - `_MAX_EVENTS_PER_JOB = 1000` in `wattpad_crawler/web/runner.py`
  - `_MAX_JOBS = 50` in `wattpad_crawler/web/runner.py`
- **D-12:** `_parse_one(raw, depth=0, max_depth=_MAX_COMMENT_DEPTH)` — depth is a positional param so recursive calls can increment; `max_depth` is a keyword param defaulting to the module constant so tests can pass `max_depth=3` per call.
- **D-13:** **JobManager pruning preserves running jobs:** when `create()` would push count over `_MAX_JOBS`, iterate `_order` from oldest forward and evict only jobs with `status in {done, failed}`. Running jobs are pinned. Cap may be temporarily exceeded if many jobs are running simultaneously.

**Render failure & comment truncation (REL-04 + REL-01):**
- **D-14:** New `RenderError(Exception)` lives in `wattpad_crawler/jobs.py` alongside `ResolveError`.
- **D-15:** `archive_story()` render section restructures to:
  1. Build `render_status: dict[str, Literal["ok", "failed"]]` covering txt/html/epub
  2. For each renderer: try → mark `ok`; except → mark `failed`, emit `render.failed` (existing event)
  3. Emit `story.done` with `render_status` field included
  4. After the loop, if all three values are `"failed"`, raise `RenderError(f"all renders failed: {render_status}")`
- **D-16:** **Exception propagation:** JobRunner already catches `Exception` → `job.set_failed`, so `RenderError` flows through unchanged. `archive_many()` already catches per-story exceptions and records `failed: {e}` in the batch results dict — same path.
- **D-17:** **Comment truncation semantics:** at `depth >= max_depth`, the parent comment is preserved with `replies=[]`. Deeper-nested replies are dropped entirely (no synthetic placeholder).
- **D-18:** **Truncation warning frequency:** `_parse_one` tracks whether truncation occurred during its recursive descent (return value or thread-local counter — planner's choice). At each top-level comment, if its subtree was truncated, emit one `logger.warning` including the parent comment id and the dropped reply count.

### Claude's Discretion

- **Exact nh3 API surface** — `nh3.clean()` vs. `nh3.Cleaner(...)` instance; `data-p-id` allowlisting mechanism. **Resolved by this research** (see `## Standard Stack` and `## Code Examples`).
- **Truncation tracking mechanism** — return-tuple `(comment, was_truncated)` vs. mutable counter passed in vs. exception-based. **Recommendation in this research** (see `## Architecture Patterns: Truncation Tracking`).
- **Test fixture shapes** — synthetic 15-level reply chain, JSON paragraph with `<script>`/`<img onerror=...>`, 1100-event Job, 60-job submit, 3-renderer-failure story. **Sketches in this research** (see `## Code Examples: Test Fixtures`).
- **Eviction-warning event payload** — specifically what to put in `events.evicted` data dict. **Recommendation in this research** (see `## Architecture Patterns: Eviction Event Payload`).

### Deferred Ideas (OUT OF SCOPE)

- **Comment body sanitization** — defer until comment-injection is observed in the wild. Phase 1 stays focused on paragraph HTML per SAN-01.
- **`nh3` allowlist tuning per-format** — different allowlists for EPUB vs. HTML is theoretical; one allowlist for the stored data is sufficient.
- **Persist evicted events to disk** — out of scope; ephemeral memory cap is the chosen trade-off.
- **`max_jobs_in_memory` as a config field** — only matters if a future v2 introduces multi-user / longer-running web sessions.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **REL-01** | `_parse_one()` accepts `max_depth` param (default 10), truncates deeper replies, logs a warning. | `tuple[T, bool]` return idiom is precedented (`parse_comments_page` already returns `tuple[list[Comment], str | None]`). Wrapper at `parse_comments_page` aggregates per-top-level-comment truncation flags and emits one `logger.warning` per truncated subtree. See `## Architecture Patterns: Truncation Tracking`. |
| **REL-02** | `Job.events` capped at most-recent N entries (default 1000); SSE still emits new events. | `collections.deque(maxlen=...)` evicts left atomically on append under GIL. `Job._lock` already serializes `events.append`; the change is type-only at the call site. SSE uses new `seq`-based snapshot. See `## Architecture Patterns: Deque Eviction` and `## Code Examples: Job dataclass`. |
| **REL-03** | `JobManager` retains only N most recent jobs (default 50), prunes under existing lock when new job is created. | Existing `JobManager._lock` covers `_jobs` and `_order`. `JobRunner._running` set already tracks active jobs and is queryable via `running_count()`; pruning predicate `status in {done, failed}` is sufficient without coordinating with the runner. See `## Code Examples: JobManager.create with prune`. |
| **REL-04** | If all three renderers fail, job ends `failed` not `done`; partial success surfaces as per-format flag. | `JobRunner._run` already catches `Exception` and routes to `set_failed(str(e))`. New `RenderError(Exception)` raised inside `archive_story` after the render loop flows through the existing path. `story.done` event payload extended with `render_status` dict. See `## Code Examples: render_status loop`. |
| **SAN-01** | `extract_chapter()` runs each paragraph's `html` through nh3 allowlist preserving `<img>`, `<br>`, `data-p-id`. | `nh3.Cleaner` instantiated once at module scope; called inside the existing `for para in para_els:` loop. Allowlist is `{"img","br","b","i","em","strong","u","a"}`; per-tag attrs `{"img":{"src","alt"},"a":{"href"}}`; universal `{"*":{"data-p-id"}}`. URL schemes default to `{"http","https",...}` which already covers D-02; no override needed. See `## Code Examples: extract_chapter sanitizer`. |
| **SAN-02** | `nh3 0.3.x` added; `bleach` not introduced. | `nh3 >= 0.3, < 0.4` resolves to 0.3.5 as of Apr 2026 [VERIFIED: PyPI release page]. `Cleaner` API was introduced in 0.3.0 (Jul 2024) — pinning to 0.3 is exactly right. See `## Standard Stack`. |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `nh3` | `>=0.3,<0.4` | HTML sanitization at extract-time | Rust-backed (ammonia); `Cleaner` class added in 0.3.0 for reusable allowlists; 0.3.5 is current as of Apr 2026 [VERIFIED: PyPI release page]. Already locked by PROJECT.md and REQUIREMENTS.md SAN-02. |
| `collections.deque` | stdlib | `Job.events` bounded queue | `maxlen` evicts oldest on append; `append` is atomic under GIL. Zero new dependency. |
| `logging` | stdlib | Truncation warnings | Already used per `__name__`-scoped loggers throughout the codebase. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest` | `>=8.0` (existing) | All new unit tests | Already pinned in `[project.optional-dependencies] dev`. No `caplog` plugin needed — built into pytest. |

**Installation:** Add a single line to `pyproject.toml` `[project] dependencies` block:

```toml
dependencies = [
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "ebooklib>=0.18",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "jinja2>=3.1",
  "sse-starlette>=2.0",
  "nh3>=0.3,<0.4",            # NEW
]
```

Then `pip install -e .` in the existing `.venv`. No optional-extra dance, no dev-only install — sanitization is part of the runtime path so it belongs in `dependencies`, not `dev`.

**Version verification:** `nh3 0.3.5` released 2026-04-25 [VERIFIED: PyPI]. The `<0.4` upper bound is conservative — when 0.4.0 ships we'll re-pin then.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `nh3.Cleaner` (instance reused) | `nh3.clean(...)` per call | `clean()` reparses kwargs into a fresh ammonia builder on every call. `Cleaner` builds the underlying `ammonia::Builder` once and reuses it. For ~20–500 paragraphs per chapter this matters; `Cleaner` is the documented idiomatic shape for repeated sanitization with a fixed allowlist [CITED: nh3.readthedocs.io/en/latest/]. |
| `attributes={"*": {"data-p-id"}}` | `generic_attribute_prefixes={"data-"}` | The latter would allow *all* `data-*` attributes (including any future `data-tracking-id`). We want exactly `data-p-id`. Explicit is safer — the prefix wildcard is overkill and breaks D-03's principle of a small attribute surface. |
| `(comment, was_truncated)` tuple return | Mutable counter in closure | Tuple matches `parse_comments_page`'s existing `tuple[list[Comment], str | None]` return shape — same module, same idiom. Closure-counter requires creating a fresh container per top-level comment, which is more code than wrapping the recursive call. |
| Module-level `_MAX_*` constants | TOML-config keys | CONTEXT.md D-11 locks this. Tests monkeypatch the constants — a tested pattern (`test_jobs.py:test_archive_story_renderers_are_independent` already uses `monkeypatch.setattr` against module objects). |

## Architecture Patterns

### Recommended File Touch Map

```
wattpad_crawler/
├── api/
│   └── comments.py          # REL-01: _MAX_COMMENT_DEPTH, _parse_one(depth, max_depth), tuple-return, warn-once-per-subtree
├── web/
│   ├── runner.py            # REL-02 + REL-03: deque events, _next_seq, snapshot_events(after_seq), _MAX_EVENTS_PER_JOB, _MAX_JOBS, prune in create()
│   ├── routes.py            # D-09: rename ?after= → ?after_seq=; emit events.evicted synthetic on gap
│   └── templates/
│       └── job.html         # D-09: rename ?after= → ?after_seq= in EventSource URL
├── jobs.py                  # REL-04: RenderError(Exception); render_status dict; raise after loop if all failed
├── scrape/
│   └── chapter_html.py      # SAN-01: module-scope nh3.Cleaner; sanitize paragraphs[i]["html"]
└── ...

pyproject.toml               # SAN-02: nh3>=0.3,<0.4 in [project] dependencies

tests/unit/
├── test_api_comments.py     # REL-01 fixtures + assertions
├── test_runner.py           # REL-02 (1100-event), REL-03 (60-job submit, prune-preserves-running)
├── test_jobs.py             # REL-04 (3-renderer-failure → RenderError → status=failed)
├── test_chapter_html.py     # SAN-01 (script tag stripped, onerror stripped, data-p-id preserved, javascript: href stripped)
└── test_web_routes.py       # D-09 SSE param rename; events.evicted on gap
```

No new files; no directory restructure. Every change is additive to an existing module.

### Pattern 1: Module-Scope `nh3.Cleaner`

**What:** Construct one `Cleaner` instance at module-import time. Reuse for every paragraph in every chapter for every story for the entire process lifetime.

**When to use:** Allowlist is fixed at compile-time; many small fragments to sanitize per call. Both apply here.

**Why over per-call `clean()`:** `nh3.Cleaner` was introduced in 0.3.0 specifically to amortize the ammonia-builder construction cost across many `.clean()` invocations [CITED: github.com/messense/nh3 release notes for v0.3.0]. The README's idiomatic example for the Django integration uses module-scope `Cleaner` for exactly this case [CITED: adamj.eu/tech/2023/12/13/django-sanitize-incoming-html-nh3/].

### Pattern 2: Deque Eviction with Sequence Numbers

**What:** Replace `events: list[ProgressEvent]` with `events: deque[ProgressEvent]` (`maxlen=1000`) plus a monotonic `_next_seq: int` counter. Each `emit()` assigns `seq = self._next_seq; self._next_seq += 1` to the new event before appending.

**Why seq instead of index:** A `list` index is stable until something gets popped. A `deque(maxlen=N)` shifts all indices when it evicts. Indexes from before an eviction become wrong silently. A monotonic seq survives eviction — the SSE client passes `?after_seq=42` and we filter `[e for e in events if e.seq > 42]`. The catch: events with `seq <= oldest_in_deque` are gone forever, and the SSE handler must signal that gap to the UI (D-10 `events.evicted`).

**Thread-safety note:** `deque.append()` is atomic under CPython's GIL [VERIFIED: cpython issue #15329, github issue #112050]. The eviction that happens when `maxlen` is reached is part of the same `append` C-level call — no compound operation, no race. The existing `Job._lock` still wraps `emit()`, so reads from `snapshot_events()` see a consistent state. Recommendation: keep the lock unchanged. Removing it would technically still work for `append`, but `len(self.events)` and iteration in `snapshot_events` are not atomic compounds — keeping the lock is cheap insurance.

### Pattern 3: Truncation Tracking

**What:** `_parse_one` returns `tuple[Comment | None, bool]` where the bool is "did this subtree truncate at any depth." `parse_comments_page` collects bool flags and emits one `logger.warning` per truncated top-level comment.

**Why tuple over thread-local counter or exception:**
- **Tuple:** matches `parse_comments_page`'s existing return shape `tuple[list[Comment], str | None]` — same file, same idiom. Pure function, no globals, easy to test (the bool is an output, not a side effect).
- **Thread-local counter:** truncation is per-call, not per-thread; mismatch.
- **Exception:** truncation is *expected* behavior when caps are hit, not exceptional. Using `try/except` for control flow violates the project's "loud over silent, but exceptions only for unexpected" pattern.

The wrapper logic in `parse_comments_page` is small:

```python
def parse_comments_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    raw_comments = raw.get("comments") or []
    parsed: list[Comment] = []
    for r in raw_comments:
        if not isinstance(r, dict):
            continue
        comment, truncated = _parse_one(r)
        if comment is None:
            continue
        parsed.append(comment)
        if truncated:
            logger.warning(
                "comment truncation: replies beyond depth %d dropped under comment %s",
                _MAX_COMMENT_DEPTH, comment.comment_id,
            )
    return parsed, raw.get("nextUrl")
```

### Pattern 4: Eviction Event Payload

**What:** When SSE handler observes `after_seq < oldest_seq_in_deque`, emit a synthetic event ahead of the regular snapshot:

```python
{
  "kind": "events.evicted",
  "data": {
    "dropped_count": <int>,           # how many seqs the client missed
    "requested_after_seq": <int>,     # what they asked for
    "oldest_available_seq": <int>,    # earliest seq still in deque
  },
  "ts": <float>,                      # current time, not historical
}
```

This is synthetic — it's emitted by the SSE handler, not stored in `Job.events`. The UI displays a "older events were dropped to save memory" banner.

**`dropped_count` calculation:** `oldest_available_seq - 1 - requested_after_seq`. If client requested `after_seq=10` and oldest available is `seq=42`, then events 11..41 (31 events) were dropped.

**Why this shape:** `dropped_count` is the only field the UI actually needs to display "31 older events were dropped." `requested_after_seq` and `oldest_available_seq` are diagnostic — useful in the page source / dev tools. Keep payloads small but recoverable.

### Pattern 5: JobManager Prune Preserves Running

**What:** Inside `JobManager.create()`, after registering the new job, if `len(self._jobs) > _MAX_JOBS`, walk `self._order` from index 0 forward, removing job ids whose `Job.status` is `done` or `failed`, until the count is at-or-under cap or the list is exhausted. Running jobs (`pending`, `running`) are skipped.

**Cap-overshoot behavior:** If 60 jobs are submitted and 55 are still running, only 5 will be evictable; the dict will sit at 60. This is correct — better than orphaning a thread that's still writing to a Job object that's been removed from the registry.

**Single-lock invariant:** All of this happens inside `with self._lock:` (which already wraps the existing `_jobs[id] = job; _order.append(id)`). No coordination with `JobRunner._running` is needed because the predicate `status in {done, failed}` is sufficient — a job in those terminal states will never have its emit() or set_*() called again.

### Anti-Patterns to Avoid

- **Calling `nh3.clean()` per-paragraph instead of reusing a `Cleaner`:** measurable per-chapter overhead. The `Cleaner` API exists exactly for this case.
- **Using `generic_attribute_prefixes={"data-"}` to allow `data-p-id`:** widens the surface to all `data-*` attributes. D-02 says exactly `data-p-id`. Use `attributes={"*": {"data-p-id"}}` instead.
- **Adding `bleach` dependency:** explicitly forbidden in REQUIREMENTS.md SAN-02 and PROJECT.md "Out of Scope." `bleach` is deprecated since Jan 2023 and depends on unmaintained `html5lib`.
- **Storing the seq counter on `ProgressEvent` defaults:** the seq must be assigned by `Job.emit` under the lock so multiple concurrent emits get distinct values. Don't put `seq: int = field(default_factory=<some counter>)` — that creates a *module-level* counter shared across all jobs.
- **Pruning under a separate lock from the create lock:** would race against another thread calling `create()`. Single lock, single critical section.
- **Letting `events.evicted` be a real `Job.events` entry:** it would itself be subject to eviction. Synthetic-only, emitted by the SSE handler at stream time.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTML sanitization | Regex-based tag stripping | `nh3.Cleaner` | nh3 wraps Rust ammonia, which has been hardened against ~10 years of XSS-research edge cases. Hand-rolled regex sanitizers fail on nested tags, malformed attributes, mixed-case event handlers, U+0000 NULs in attributes, etc. |
| Bounded ring buffer | List with `pop(0)` and length check | `collections.deque(maxlen=N)` | `list.pop(0)` is O(n). `deque.append()` is O(1) including eviction. Single-bytecode atomic under GIL. |
| Thread-safe sequence counter | `itertools.count()` shared module-wide | Per-`Job` `_next_seq: int` under `Job._lock` | The counter must be per-job (each Job has its own seq space). Sharing across jobs would conflate streams and break SSE replay. |
| URL scheme validation | `urllib.parse.urlparse` + scheme check | `nh3` `url_schemes` parameter (default already correct) | nh3's default `ALLOWED_URL_SCHEMES` includes `http`, `https`, `mailto`, `tel`, etc. and excludes `javascript:` and `data:`. The default *is* the safe list — no custom code needed for D-02. |

**Key insight:** Every problem in Phase 1 has a single-import, well-supported standard solution. The only judgement call was `nh3` vs `bleach`, and that's already locked.

## Runtime State Inventory

This is a refactor/feature-addition phase, not a rename or string-replacement phase. There is no stored data, live service config, OS-registered state, secrets, or build artifact that embeds an old name and would break after the changes. Specifically:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — sanitization runs on **new** chapter HTML at extract-time. Existing archived `parts/*.json` files contain pre-sanitization paragraph HTML; per CONTEXT.md they continue to work (renderers consume whatever's stored). No re-archive of existing stories is required by Phase 1. | None |
| Live service config | None — no external services. | None |
| OS-registered state | None — no Task Scheduler tasks, pm2 processes, systemd units. | None |
| Secrets/env vars | None — `_config.toml` cookie unchanged. SSE param rename is a query-string change, not an env var change. | None |
| Build artifacts | `pyproject.toml` adds one dependency. After merge, devs must run `pip install -e .` once to get `nh3`. Any pre-existing `.venv` without nh3 will fail at import. | Document in plan: "After merge, run `pip install -e .` to install nh3." |

**The canonical question — *After every file in the repo is updated, what runtime systems still have the old string cached, stored, or registered?*** Answer: only the `?after=N` query string in already-loaded `job.html` pages in the user's browser. Hard-refresh after deploy resolves this. Single-user tool — not a real concern.

## Common Pitfalls

### Pitfall 1: nh3 strips `<a href="javascript:...">` href but keeps the `<a>` tag with empty rel

**What goes wrong:** Test expects `<a>...</a>` with no href — gets `<a rel="noopener noreferrer">...</a>`.
**Why it happens:** nh3's `link_rel` default is `'noopener noreferrer'` and is added to every `<a>` regardless of whether the href survived sanitization [VERIFIED: nh3.readthedocs.io url_schemes example].
**How to avoid:** Test assertions on `<a>` elements should accept either presence or absence of `rel`; or pin `link_rel=None` in the Cleaner config if we want a strict no-rel output. Recommendation: leave `link_rel` at default (rel=noopener-noreferrer is desirable for archived HTML opened in any reader), and have tests assert `'href="javascript:' not in result` rather than full-string equality.
**Warning signs:** Test failure where the expected string differs only by `rel="noopener noreferrer"`.

### Pitfall 2: deque iteration during eviction

**What goes wrong:** Concurrent `emit()` (which appends and may evict) while `snapshot_events()` is iterating the deque can raise `RuntimeError: deque mutated during iteration` even under the GIL.
**Why it happens:** GIL protects bytecode atomicity, not multi-statement consistency. The current code already serializes both with `Job._lock`.
**How to avoid:** Keep the existing `with self._lock:` around both `emit` and `snapshot_events`. The lock is the safety net, not the GIL.
**Warning signs:** intermittent test failures under high concurrency in `test_job_emit_is_thread_safe`-style tests.

### Pitfall 3: SSE template not updated → silent breakage

**What goes wrong:** `web/routes.py` is changed to expect `?after_seq=N`, but `templates/job.html` still emits `?after=N`. The route's `after_seq: int = 0` default kicks in, so every SSE connection replays the entire deque from seq 0 — looks like it works on small jobs, fails subtly on jobs that have evicted events (UI shows duplicate events).
**Why it happens:** Two-line change spread across two files. Easy to forget the template.
**How to avoid:** Plan must include `wattpad_crawler/web/templates/job.html` in `files_modified` (verified: only one `?after=` reference in the templates dir, line 30 of job.html). Verifier should grep for any remaining `?after=` in templates and static.
**Warning signs:** Job page shows the same event twice, or shows old events on every page reload.

### Pitfall 4: `_parse_one` recursion at depth limit drops the parent

**What goes wrong:** Misreading D-17 and treating "depth >= max_depth" as "discard this comment." Spec says: at depth == max_depth, the comment itself is preserved with `replies=[]`; only deeper-nested replies are dropped.
**Why it happens:** Off-by-one when interpreting "depth-bounded recursion."
**How to avoid:** Test with a 15-level chain at `max_depth=10`; assert that 10 levels are preserved, 11..15 are dropped. The Comment at level 10 must have `replies=[]` not be `None`.
**Warning signs:** Top-level Comment.replies suddenly contains zero items even when raw response had nested replies.

### Pitfall 5: pruning happens before insert → could prune the just-created job

**What goes wrong:** If `JobManager.create()` first prunes then inserts and the cap is set to 1, the just-created job could be evicted by the next call before its runner thread starts.
**Why it happens:** Order of operations in the critical section.
**How to avoid:** Insert first, then prune. Pruning predicate `status in {done, failed}` will skip the just-created job (status is `pending`). Document this ordering in a code comment.
**Warning signs:** Race that only shows under stress tests; running jobs get orphaned.

### Pitfall 6: `RenderError` raised inside the loop instead of after

**What goes wrong:** Raising `RenderError` on the first failure short-circuits the loop and never tries the other two renderers; partial-success info (`render_status` showing one failed, two succeeded) is lost.
**Why it happens:** Misreading D-15 step 4.
**How to avoid:** All three renderers must run unconditionally inside the loop, each in its own `try`. The `RenderError` raise is *after* the loop completes, gated on `all(v == "failed" for v in render_status.values())`. Existing test `test_archive_story_renderers_are_independent` already verifies this independence behavior — extend it for the all-fail case.
**Warning signs:** A story where TXT renders successfully but HTML and EPUB fail produces no `.html` or `.epub` artifacts and the job is `failed` (correct behavior is `done` with `render_status={"txt":"ok","html":"failed","epub":"failed"}`).

## Code Examples

### Module-scope nh3 Cleaner in `chapter_html.py`

```python
# Source: nh3.readthedocs.io/en/latest/ + locked decisions D-01 .. D-04
import logging
from dataclasses import dataclass

import nh3
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Phase 1 SAN-01: paragraph HTML allowlist. Reading-rich (D-01) plus
# data-p-id on every tag (D-02) and the project's own CSS handles
# styling so class/style are stripped (D-03).
_PARAGRAPH_CLEANER = nh3.Cleaner(
    tags={"img", "br", "b", "i", "em", "strong", "u", "a"},
    attributes={
        "img": {"src", "alt"},
        "a": {"href"},
        "*": {"data-p-id"},
    },
    # url_schemes default already excludes javascript:/data:; non-matching
    # schemes strip the attribute, leaving the tag intact. D-02 spec.
    strip_comments=True,
)


@dataclass
class ChapterContent:
    text: str
    paragraphs: list[dict]
    images: list[str]


def extract_chapter(html: str) -> ChapterContent:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(".page-container") or soup.body
    if container is None:
        return ChapterContent(text="", paragraphs=[], images=[])

    paragraphs: list[dict] = []
    images: list[str] = []
    para_els = container.find_all(attrs={"data-p-id": True})
    if not para_els:
        logger.warning(
            "extract_chapter: no elements with data-p-id found; "
            "Wattpad HTML structure may have changed"
        )

    for para in para_els:
        pid = para.get("data-p-id", "")
        for img in para.find_all("img"):
            src = img.get("src", "")
            if src:
                images.append(src)
        raw_html = para.decode_contents()
        clean_html = _PARAGRAPH_CLEANER.clean(raw_html)   # SAN-01
        paragraphs.append({
            "id": pid,
            "text": para.get_text(" ", strip=True),
            "html": clean_html,
        })
    text = "\n\n".join(p["text"] for p in paragraphs if p["text"])
    return ChapterContent(text=text, paragraphs=paragraphs, images=images)
```

### `_parse_one` with depth cap and tuple return

```python
# Source: locked decisions D-11, D-12, D-17, D-18 + existing tuple-return
# precedent at parse_comments_page.
import logging
from typing import Any
from urllib.parse import quote

from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.models import Comment

logger = logging.getLogger(__name__)

INLINE_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100"
END_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100&forms=root"
_MAX_PAGES = 200
_MAX_COMMENT_DEPTH = 10


def _parse_one(
    raw: dict[str, Any],
    depth: int = 0,
    *,
    max_depth: int = _MAX_COMMENT_DEPTH,
) -> tuple[Comment | None, bool]:
    """Parse a single comment dict.

    Returns (comment_or_None, truncated_flag).
    `truncated_flag` is True if any reply at any depth in this subtree was
    dropped because depth >= max_depth.
    """
    cid = raw.get("id")
    if cid is None:
        return None, False

    user_obj = raw.get("user")
    user = user_obj.get("name", "") if isinstance(user_obj, dict) else ""

    truncated = False
    if depth >= max_depth:
        replies: list[Comment] = []
        # If the raw payload had any replies, mark truncation.
        if raw.get("replies"):
            truncated = True
    else:
        replies_raw = raw.get("replies") or []
        replies = []
        for r in replies_raw:
            if not isinstance(r, dict):
                continue
            child, child_trunc = _parse_one(r, depth + 1, max_depth=max_depth)
            if child is not None:
                replies.append(child)
            if child_trunc:
                truncated = True

    return Comment(
        comment_id=str(cid),
        user=user,
        body=raw.get("body") or "",
        created_at=raw.get("createdAt") or "",
        paragraph_id=raw.get("paragraphId"),
        replies=replies,
    ), truncated


def parse_comments_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    raw_comments = raw.get("comments") or []
    parsed: list[Comment] = []
    for r in raw_comments:
        if not isinstance(r, dict):
            continue
        comment, was_truncated = _parse_one(r)
        if comment is None:
            continue
        parsed.append(comment)
        if was_truncated:
            logger.warning(
                "comment %s truncated at depth %d (replies dropped)",
                comment.comment_id, _MAX_COMMENT_DEPTH,
            )
    return parsed, raw.get("nextUrl")
```

### `Job` dataclass with deque + seq counter

```python
# Source: locked decisions D-06 through D-10, D-11, D-13.
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_MAX_EVENTS_PER_JOB = 1000
_MAX_JOBS = 50


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, Any]
    seq: int = 0                          # NEW — assigned by Job.emit()
    timestamp: float = field(default_factory=time.time)


@dataclass
class Job:
    job_id: str
    kind: str
    args: dict[str, Any]
    status: JobStatus = JobStatus.pending
    events: deque[ProgressEvent] = field(
        default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB)
    )
    error: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    _next_seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._next_seq += 1
            self.events.append(
                ProgressEvent(kind=kind, data=data or {}, seq=self._next_seq)
            )
            self._wake.set()

    # ... set_running / set_done / set_failed unchanged ...

    def snapshot_events(self, after_seq: int = 0) -> list[ProgressEvent]:
        """Return events whose seq > after_seq (atomic snapshot)."""
        with self._lock:
            return [e for e in self.events if e.seq > after_seq]

    def oldest_seq(self) -> int:
        """Return the seq of the oldest event still in the deque, or 0
        if empty. Used by the SSE handler to detect eviction gaps."""
        with self._lock:
            return self.events[0].seq if self.events else 0
```

### `JobManager.create()` with prune

```python
class JobManager:
    """In-memory registry of jobs. Thread-safe."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def create(self, kind: str, args: dict[str, Any]) -> Job:
        job = Job(job_id=new_job_id(), kind=kind, args=args)
        with self._lock:
            # Insert first so the new job is never a prune candidate.
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            # Prune oldest-finished until at-or-under cap or no more
            # finished jobs to evict. Running jobs are pinned (D-13).
            if len(self._jobs) > _MAX_JOBS:
                survivors: list[str] = []
                pruned = 0
                for jid in self._order:
                    j = self._jobs.get(jid)
                    if j is None:
                        continue
                    if (
                        len(self._jobs) - pruned > _MAX_JOBS
                        and j.status in (JobStatus.done, JobStatus.failed)
                    ):
                        del self._jobs[jid]
                        pruned += 1
                    else:
                        survivors.append(jid)
                self._order = survivors
        return job
```

### `archive_story` render-section rework

```python
# Source: locked decisions D-14, D-15, D-16.

class RenderError(Exception):
    """All renderers (TXT, HTML, EPUB) failed for one story."""
    pass


# inside archive_story(...), replace the existing render block:
sd = store.story_dir(cfg.output_dir, story)
emit("render.start", {"story_id": story.story_id})
render_status: dict[str, str] = {}              # "ok" | "failed"
for name, fn in (
    ("txt", render_txt.render_txt),
    ("html", render_html.render_html),
    ("epub", render_epub.render_epub),
):
    try:
        fn(sd)
        render_status[name] = "ok"
    except Exception as e:
        logger.exception("render(%s) failed for %s: %s", name, story.story_id, e)
        emit("render.failed", {"format": name, "error": str(e)})
        render_status[name] = "failed"

emit("story.done", {"story_id": story.story_id, "render_status": render_status})

if all(v == "failed" for v in render_status.values()):
    raise RenderError(f"all renders failed: {render_status}")
```

Note: `story.done` is emitted *before* the raise so the SSE stream sees the per-format breakdown even when the job ends `failed`. The existing JobRunner contract is "set_failed records the exception string" — it does not affect prior emits.

### SSE handler with `?after_seq=` and eviction event

```python
# Source: locked decisions D-08, D-09, D-10.

@router.get("/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: str, after_seq: int = 0):
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        import asyncio
        last_seq = after_seq
        gap_announced = False
        while True:
            if await request.is_disconnected():
                break

            # Detect eviction gap on first poll: client asked for events
            # after a seq that has already been evicted from the deque.
            if not gap_announced:
                oldest = job.oldest_seq()
                if oldest and last_seq + 1 < oldest:
                    dropped = oldest - 1 - last_seq
                    yield {
                        "data": json.dumps({
                            "kind": "events.evicted",
                            "data": {
                                "dropped_count": dropped,
                                "requested_after_seq": after_seq,
                                "oldest_available_seq": oldest,
                            },
                            "ts": time.time(),
                        }),
                    }
                gap_announced = True

            new_events = job.snapshot_events(last_seq)
            for ev in new_events:
                last_seq = ev.seq
                yield {
                    "data": json.dumps({
                        "kind": ev.kind, "data": ev.data,
                        "seq": ev.seq, "ts": ev.timestamp,
                    })
                }
            if job.status.value in ("done", "failed"):
                yield {
                    "data": json.dumps({
                        "kind": "__status__",
                        "data": {"status": job.status.value, "error": job.error},
                    })
                }
                return
            await asyncio.sleep(0.25)

    return EventSourceResponse(event_gen())
```

### Template change in `job.html` (line 30)

```html
<!-- BEFORE -->
var es = new EventSource("/jobs/{{ job.job_id }}/stream?after={{ job.events|length }}");

<!-- AFTER -->
var es = new EventSource("/jobs/{{ job.job_id }}/stream?after_seq={{ job._next_seq }}");
```

Note: `{{ job.events|length }}` is wrong post-deque because length is bounded; the correct "I've seen up to here" cursor is `job._next_seq` (the highest seq assigned so far). Dataclass field access in Jinja2 is just attribute access — `job._next_seq` works.

### Test Fixtures

**REL-01 — 15-level reply chain:**

```python
# tests/unit/test_api_comments.py

def _nest(level: int) -> dict:
    """Build a comment dict with `level` levels of nested replies."""
    if level == 0:
        return {"id": f"c{level}", "body": "leaf", "user": {"name": "u"}}
    return {
        "id": f"c{level}",
        "body": f"level {level}",
        "user": {"name": "u"},
        "replies": [_nest(level - 1)],
    }


def test_parse_one_caps_recursion_at_max_depth():
    from wattpad_crawler.api.comments import _parse_one
    raw = _nest(15)
    comment, truncated = _parse_one(raw, max_depth=10)
    assert comment is not None
    assert truncated is True

    # Walk down 10 levels, expect replies; on the 11th level expect [].
    cursor = comment
    for i in range(10):
        assert cursor is not None
        assert len(cursor.replies) == 1
        cursor = cursor.replies[0]
    assert cursor.replies == []  # truncated here


def test_parse_comments_page_logs_warning_on_truncation(caplog):
    import logging
    from wattpad_crawler.api.comments import parse_comments_page
    raw = {"comments": [_nest(15)], "nextUrl": None}
    with caplog.at_level(logging.WARNING, logger="wattpad_crawler.api.comments"):
        parsed, _ = parse_comments_page(raw)
    assert len(parsed) == 1
    assert any("truncat" in rec.message.lower() for rec in caplog.records)


def test_parse_one_no_recursion_error_on_deep_chain():
    """30-level chain must not raise RecursionError even at default cap."""
    from wattpad_crawler.api.comments import _parse_one
    comment, truncated = _parse_one(_nest(30))
    assert comment is not None
    assert truncated is True
```

**SAN-01 — script + onerror + javascript: + data-p-id preserved:**

```python
# tests/unit/test_chapter_html.py — new tests

def test_extract_chapter_strips_script_in_paragraph():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">hi <script>alert(1)</script>safe</pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert "<script>" not in result.paragraphs[0]["html"].lower()
    assert "alert" not in result.paragraphs[0]["html"]
    assert "safe" in result.paragraphs[0]["html"]


def test_extract_chapter_strips_onerror_handler():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><img src="x.jpg" onerror="alert(1)"></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert "onerror" not in result.paragraphs[0]["html"].lower()
    assert "<img" in result.paragraphs[0]["html"].lower()


def test_extract_chapter_preserves_data_p_id_attribute():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><span data-p-id="inner">child</span></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    # Outer paragraph's id field already captures p1; the html field
    # contains the inner span which should still have data-p-id.
    assert 'data-p-id="inner"' in result.paragraphs[0]["html"]


def test_extract_chapter_strips_javascript_href_keeps_link_text():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><a href="javascript:alert(1)">click</a></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "javascript:" not in h.lower()
    assert "click" in h


def test_extract_chapter_preserves_https_href():
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><a href="https://example.com">x</a></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    assert 'href="https://example.com"' in result.paragraphs[0]["html"]


def test_extract_chapter_preserves_bold_italic():
    """D-01: reading-rich allowlist preserves <b>, <i>, <em>, <strong>."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1">a <b>bold</b> and <em>emph</em> word</pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "<b>" in h and "</b>" in h
    assert "<em>" in h and "</em>" in h


def test_extract_chapter_strips_class_and_style():
    """D-03: class and style stripped from every tag."""
    html = (
        '<html><body><div class="page-container">'
        '<pre data-p-id="p1"><img src="a.jpg" class="hero" style="width:100%"></pre>'
        '</div></body></html>'
    )
    result = extract_chapter(html)
    h = result.paragraphs[0]["html"]
    assert "class=" not in h
    assert "style=" not in h
    assert 'src="a.jpg"' in h
```

**REL-02 — 1100-event Job:**

```python
# tests/unit/test_runner.py — new tests

def test_job_events_capped_at_max_events_per_job(monkeypatch):
    from wattpad_crawler.web import runner
    monkeypatch.setattr(runner, "_MAX_EVENTS_PER_JOB", 1000)
    # Re-instantiate Job's deque with the patched cap by constructing a fresh one
    job = runner.Job(job_id="j1", kind="archive_story", args={})
    # The Job dataclass picks up _MAX_EVENTS_PER_JOB at import time, so for
    # the live test we patch the deque's maxlen at fixture time:
    job.events = __import__("collections").deque(maxlen=1000)
    for i in range(1100):
        job.emit("tick", {"i": i})
    assert len(job.events) == 1000
    # Oldest 100 evicted; remaining seqs are 101..1100.
    assert job.events[0].seq == 101
    assert job.events[-1].seq == 1100


def test_job_snapshot_events_uses_seq_filter():
    job = Job(job_id="j1", kind="x", args={})
    for i in range(5):
        job.emit("tick", {"i": i})
    # snapshot from seq 2 returns events with seq 3, 4, 5
    snap = job.snapshot_events(after_seq=2)
    assert [e.seq for e in snap] == [3, 4, 5]
```

**REL-03 — 60-job submit, prune preserves running:**

```python
def test_jobmanager_prunes_old_finished_jobs(monkeypatch):
    from wattpad_crawler.web import runner
    monkeypatch.setattr(runner, "_MAX_JOBS", 5)
    mgr = runner.JobManager()
    jobs = [mgr.create("k", {}) for _ in range(5)]
    for j in jobs:
        j.set_done()
    # Sixth create should evict the oldest.
    j6 = mgr.create("k", {})
    assert len(mgr._jobs) == 5
    assert jobs[0].job_id not in mgr._jobs
    assert j6.job_id in mgr._jobs


def test_jobmanager_pruning_preserves_running_jobs(monkeypatch):
    from wattpad_crawler.web import runner
    monkeypatch.setattr(runner, "_MAX_JOBS", 3)
    mgr = runner.JobManager()
    j1 = mgr.create("k", {})  # will stay running
    j2 = mgr.create("k", {}); j2.set_done()
    j3 = mgr.create("k", {}); j3.set_done()
    # j1 is still pending/running; create another, then another
    j4 = mgr.create("k", {})  # 4 jobs, cap=3, j2 evicted (oldest done)
    assert j1.job_id in mgr._jobs
    assert j2.job_id not in mgr._jobs
    j5 = mgr.create("k", {})  # 4 jobs again, j3 evicted
    assert j1.job_id in mgr._jobs
    assert j3.job_id not in mgr._jobs


def test_jobmanager_overshoots_when_all_running(monkeypatch):
    from wattpad_crawler.web import runner
    monkeypatch.setattr(runner, "_MAX_JOBS", 3)
    mgr = runner.JobManager()
    js = [mgr.create("k", {}) for _ in range(5)]  # all pending
    # All 5 still present because none are done/failed.
    assert len(mgr._jobs) == 5
```

**REL-04 — 3-renderer-failure story:**

```python
# tests/unit/test_jobs.py — new test

def test_archive_story_raises_render_error_when_all_renderers_fail(
    output_dir: Path, monkeypatch,
):
    from wattpad_crawler.jobs import RenderError
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    monkeypatch.setattr(render_txt_mod,  "render_txt",  MagicMock(side_effect=RuntimeError("txt fail")))
    monkeypatch.setattr(render_html_mod, "render_html", MagicMock(side_effect=RuntimeError("html fail")))
    monkeypatch.setattr(render_epub_mod, "render_epub", MagicMock(side_effect=RuntimeError("epub fail")))

    events: list[tuple[str, dict]] = []
    with pytest.raises(RenderError) as ei:
        archive_story(
            cfg, fake_client, manifest, "42",
            deps=deps,
            progress=lambda k, d: events.append((k, d)),
        )
    assert "txt" in str(ei.value) and "html" in str(ei.value) and "epub" in str(ei.value)
    # story.done was emitted with render_status before the raise.
    done = next(d for k, d in events if k == "story.done")
    assert done["render_status"] == {"txt": "failed", "html": "failed", "epub": "failed"}
    manifest.close()


def test_archive_story_partial_render_failure_does_not_raise(
    output_dir: Path, monkeypatch,
):
    """Two of three failing is partial — story.done with render_status, no RenderError."""
    from wattpad_crawler.render import epub as render_epub_mod
    from wattpad_crawler.render import html as render_html_mod
    from wattpad_crawler.render import txt as render_txt_mod

    cfg = Config(output_dir=output_dir)
    manifest = Manifest(output_dir).connect()
    story = Story(
        story_id="42", title="Hi", author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
    )
    fake_client = MagicMock()
    deps = _make_deps(story)

    monkeypatch.setattr(render_txt_mod,  "render_txt",  MagicMock())  # ok
    monkeypatch.setattr(render_html_mod, "render_html", MagicMock(side_effect=RuntimeError("html fail")))
    monkeypatch.setattr(render_epub_mod, "render_epub", MagicMock(side_effect=RuntimeError("epub fail")))

    events: list[tuple[str, dict]] = []
    archive_story(
        cfg, fake_client, manifest, "42",
        deps=deps, progress=lambda k, d: events.append((k, d)),
    )  # must not raise
    done = next(d for k, d in events if k == "story.done")
    assert done["render_status"] == {"txt": "ok", "html": "failed", "epub": "failed"}
    manifest.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `bleach` for Python HTML sanitization | `nh3` (Rust-backed ammonia bindings) | bleach EOL announced Jan 2023; nh3 1.0 path active | nh3 is ~5–15x faster, actively maintained, no `html5lib` dependency. PROJECT.md and REQUIREMENTS.md SAN-02 already lock this. |
| `nh3.clean(html, **kwargs)` per fragment | `nh3.Cleaner(**kwargs).clean(html)` reused | nh3 0.3.0 (Jul 2024) | Amortizes ammonia builder construction; idiomatic for repeated sanitization with fixed allowlist. |
| Plain Python `list` for in-memory event queue | `collections.deque(maxlen=N)` | stdlib forever | O(1) eviction; atomic `append` under GIL; signaled by REL-02. |

**Deprecated/outdated:**
- `bleach`: deprecated since Jan 2023. Do not add. PROJECT.md "Out of Scope" enforces this.
- `pytest-vcr`: existing dev dep is on the legacy package; Phase 5 (TEST-01) replaces with `pytest-recording`. **Out of scope for Phase 1** — flagged here only so the planner doesn't accidentally touch dev deps when adding nh3.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| (none) | All factual claims about nh3 API surface, default URL schemes, `Cleaner` performance pattern, deque GIL atomicity, and existing project conventions are verified against either nh3 docs, ammonia docs, CPython issue tracker, or grep of the local codebase. | — | — |

This research has no `[ASSUMED]` claims — every recommendation is either confirmed by external documentation or pulled directly from the existing codebase. The planner can lock all decisions in this research without further user confirmation.

## Open Questions (RESOLVED)

All three open questions surfaced during research were resolved during planning. Each plan adopts the recommended answer; the resolutions are recorded inline below for traceability.

1. **Should `_PARAGRAPH_CLEANER` set `link_rel=None` to suppress nh3's default `rel="noopener noreferrer"` injection?**
   - What we know: nh3 adds `rel="noopener noreferrer"` to every `<a>` by default, even when href is stripped due to invalid scheme.
   - What's unclear: whether the user wants this rel attribute in stored archive HTML or in EPUB output. CONTEXT.md doesn't address it.
   - Recommendation: **leave `link_rel` at default**. `noopener noreferrer` is desirable for archived HTML opened in a browser — it's defense-in-depth even though our paragraph HTML has already been sanitized. The cost is one extra attribute per `<a>`. If the EPUB renderer or HTML renderer dislikes the `rel` attribute, we can revisit, but no current evidence suggests they do.
   - **RESOLVED:** Plan 01 (`01-01-PLAN.md`) follows this recommendation. `_PARAGRAPH_CLEANER = nh3.Cleaner(...)` is constructed without overriding `link_rel`, so the nh3 default of `"noopener noreferrer"` is retained on every `<a>` tag in stored paragraph HTML. No CONTEXT.md decision was added because the recommendation has zero downside; it can be revisited in a later phase if a renderer reports issues.

2. **Should the SSE `events.evicted` event be emitted *only on first poll* or every time a gap is detected?**
   - What we know: a single SSE connection has one `after_seq` from the client, and `last_seq` advances locally on the server side. A gap can only exist on the first poll — subsequent polls always have `last_seq` >= `oldest_seq` because we advance through every event we yield.
   - What's unclear: whether reconnection by the JS client (after a network blip) creates a *new* SSE handler with a fresh `gap_announced=False`, in which case the user could see two `events.evicted` for the same job. Probably fine — they reflect different points in time.
   - Recommendation: keep `gap_announced` as a per-stream bool. Document the reconnection behavior in a code comment.
   - **RESOLVED:** Plan 03 (`01-03-PLAN.md`, after the merge of the SSE-rename task) follows this recommendation. The `event_gen` async generator inside `wattpad_crawler/web/routes.py:job_stream` declares `gap_announced = False` as a function-local variable; once the synthetic `events.evicted` event is yielded (or the gap check passes), `gap_announced` is set to `True` and the gap branch is never re-entered for the lifetime of that SSE connection. Reconnection creates a fresh `event_gen` invocation and re-evaluates the gap by design. A code comment in the handler documents this.

3. **`Job._next_seq` Jinja2 access** — accessing a leading-underscore attribute from a template feels weird.
   - Recommendation: rename to `next_seq: int = 0` (no leading underscore) or expose a property `@property def next_seq(self): return self._next_seq`. The attribute *is* used as part of the API surface to the template — leading underscore implies "internal" which is no longer accurate. **Suggest renaming to `next_seq`** in the plan; trivial cost, clearer intent.
   - **RESOLVED:** Plan 03 (`01-03-PLAN.md`) follows this recommendation. The `Job` dataclass declares `next_seq: int = 0` as a public field (no leading underscore), and the file does not contain the substring `_next_seq` anywhere. Plan 05 (`01-05-PLAN.md`) consumes the public attribute via Jinja2 in `wattpad_crawler/web/templates/job.html` as `?after_seq={{ job.next_seq }}`.

## Project Constraints (from CLAUDE.md)

The project's `CLAUDE.md` enforces conventions that this phase must adhere to:

- **Python 3.11+** target — use pipe-syntax unions (`Comment | None`, `int | None`), not `Optional[X]`. New code in this phase already follows this.
- **`ruff format` + `ruff check`** for linting — line length 100, rules `["E", "F", "I", "UP", "W"]`. Plan should run `ruff` after edits.
- **`pyproject.toml` is single source of truth** for deps — adding nh3 goes in `[project] dependencies`, not a separate `requirements.txt`.
- **Backwards compatibility:** `_state.sqlite` schema **must not change** in this phase. Confirmed: none of REL-01..04, SAN-01..02 require schema changes.
- **Concurrency stays single-process; threading via `concurrent.futures.ThreadPoolExecutor`** — Phase 1 doesn't add executors but the `Job._lock` + deque pattern is consistent with this.
- **Custom exception classes inherit from `Exception` directly** — `RenderError(Exception)` matches `ResolveError(Exception)`. Don't introduce a `JobError` base class.
- **Use `pathlib.Path` everywhere** — Phase 1 does not introduce new path operations.
- **`logger = logging.getLogger(__name__)` per module** — already established; reuse in all new code.
- **`@dataclass` for value objects, `frozen=True` only for immutable configs** — `ProgressEvent` stays mutable (gets `seq` assigned post-construct in `Job.emit`), `Job` stays mutable. Match existing style.
- **Naming convention: leading-underscore UPPER_CASE for private module constants** — `_MAX_COMMENT_DEPTH`, `_MAX_EVENTS_PER_JOB`, `_MAX_JOBS` follow established `_MAX_PAGES` precedent.
- **Wattpad ToS:** changes must not increase visibility — Phase 1 changes are entirely internal (sanitization, in-memory caps, error handling). No HTTP-layer changes. ✓

## Sources

### Primary (HIGH confidence)
- `nh3.readthedocs.io/en/latest/` — `clean()` and `Cleaner` signatures; `attributes={"*": ...}` syntax; `url_schemes` strips attribute not element; `attribute_filter` callable signature; `<script>` content fully removed; `onerror` attribute stripped while preserving `<img>` element.
- `github.com/messense/nh3/releases` — v0.3.0 introduced `Cleaner`; v0.3.5 current as of Apr 2026; all 0.3.x are minor/patch from a stable API.
- `pypi.org/project/nh3/0.3.5/` — `nh3 0.3.5` released 2026-04-25.
- `docs.rs/ammonia/latest/ammonia/struct.Builder.html` — default URL scheme list: `bitcoin, ftp, ftps, geo, http, https, im, irc, ircs, magnet, mailto, mms, mx, news, nntp, openpgp4fpr, sip, sms, smsto, ssh, tel, url, webcal, wtai, xmpp`.
- Local codebase grep — `_MAX_PAGES = 200` precedent in `api/comments.py:9` and `api/user.py:19`; `tuple[..., ...]` return precedent in `parse_comments_page`; `monkeypatch.setattr` precedent in `test_jobs.py:test_archive_story_renderers_are_independent`; existing `Job._lock`-wrapped `events.append` pattern in `web/runner.py`.

### Secondary (MEDIUM confidence)
- `bugs.python.org/issue15329` and `github.com/python/cpython/issues/112050` — `deque.append()` atomicity under GIL confirmed; free-threaded build caveat noted but inapplicable (project pins 3.11+, not free-threaded).
- `adamj.eu/tech/2023/12/13/django-sanitize-incoming-html-nh3/` — module-scope `Cleaner` is the documented idiomatic pattern.
- `daniel.feldroy.com/posts/2023-06-converting-from-bleach-to-nh3` — bleach→nh3 migration notes; ~5x speedup quote.

### Tertiary (LOW confidence)
- (none — no LOW-confidence claims in this research)

## Metadata

**Confidence breakdown:**
- Standard stack (nh3 API): HIGH — verified against nh3 docs and ammonia docs.
- Architecture (deque, seq, prune): HIGH — locked in CONTEXT.md, code shapes verified against existing patterns.
- Truncation tracking (tuple): HIGH — matches existing `parse_comments_page` shape.
- Pitfalls: HIGH — derived from documented nh3 behavior and observed code structure.
- Test fixture sketches: MEDIUM — sketches are reasonable starting points; planner may refine signatures based on actual `caplog` and `monkeypatch` integration.

**Research date:** 2026-05-03
**Valid until:** 2026-06-03 (30 days — `nh3` API is stable, no upcoming breaking changes signaled in the 0.3.x line).
