# Phase 1: Local hardening fixes - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-03
**Phase:** 01-local-hardening-fixes
**Areas discussed:** Sanitization allowlist scope, Event-cap eviction under live SSE clients, Configurability of caps, Render failure & comment truncation semantics

---

## Sanitization allowlist scope (SAN-01)

### Q1: Which allowlist for paragraph HTML sanitization?

| Option | Description | Selected |
|--------|-------------|----------|
| Reading-rich | Tags: img, br, b, i, em, strong, u, a. Attrs: img[src,alt], a[href], all elements get data-p-id. Link href validated to http(s):// only. Preserves Wattpad's inline formatting in EPUB/HTML. | ✓ |
| Spec-minimum | Tags: img, br only. Attrs: img[src,alt], data-p-id on any element. Strips all bold/italic/links — chapters become plain text with images. Matches the literal letter of REQUIREMENTS.md. | |
| Permissive | Reading-rich plus blockquote, ul, ol, li, p, span, hr. Closer to a general-purpose HTML allowlist; more surface for surprising content but covers any niche tag Wattpad emits. | |

**User's choice:** Reading-rich (Recommended)

### Q2: On `class` and `style` attributes — allow or strip?

| Option | Description | Selected |
|--------|-------------|----------|
| Strip both | No class, no style. Smallest XSS surface. Wattpad styling is lost, but renders use our CSS/EPUB stylesheet anyway. Bold/italic still preserved via `<b>`/`<i>`/`<em>`/`<strong>`. | ✓ |
| Allow class, strip style | Class names pass through; inline style is stripped. Class names alone can't execute code. | |
| Allow both | Preserve inline style and class fully. Larger XSS surface; nh3 still blocks expression()/url(javascript:) inside style by default but it's a wider attack surface. | |

**User's choice:** Strip both (Recommended)

### Q3: Where in the pipeline does sanitization run?

| Option | Description | Selected |
|--------|-------------|----------|
| In extract_chapter | Sanitize each paragraph's html field inside scrape/chapter_html.py before building the ChapterContent. Stored JSON is already clean; renderers read pre-sanitized data. Matches REQUIREMENTS.md SAN-01 wording exactly. | ✓ |
| In a render-side helper only | Storage stays raw; sanitization runs only when HTML/EPUB renderers read paragraph html. Lets us re-sanitize old archives by changing one place. But raw_html.html on disk stays unsafe. | |
| Both (extract AND render) | Defence in depth. Extra CPU cost on already-trusted JSON, but resilient if extraction sanitizer is ever bypassed. | |

**User's choice:** In extract_chapter (Recommended)

### Q4: Should we also sanitize comment `body` text?

| Option | Description | Selected |
|--------|-------------|----------|
| Skip — out of scope | REQUIREMENTS.md SAN-01 names "paragraph HTML" specifically. Comments are read in the web UI as text and in EPUB they're rendered escaped already. Defer to a future hardening pass if comment-injection ever surfaces. | ✓ |
| Sanitize comment body too | Run nh3 on Comment.body during parse_comments_page. Adds CPU cost on every comment fetched (potentially thousands per chapter); broadens scope of this phase. | |

**User's choice:** Skip — out of scope (Recommended)

---

## Event-cap eviction under live SSE clients (REL-02)

### Q1: How should the event cap work so SSE clients keep receiving events correctly?

| Option | Description | Selected |
|--------|-------------|----------|
| Deque + monotonic counter | Use collections.deque(maxlen=1000) plus a self._next_seq counter. snapshot_events(after_seq) returns events whose seq > after_seq. SSE client tracks seq instead of index. Correct under eviction. | ✓ |
| List + cap-on-append, accept stale clients | self.events stays a list, append + slice [-1000:] when len exceeds cap. SSE clients reconnecting after eviction may see no new events for a few seconds. Simplest. | |
| Cap on read, not on write | events list grows unbounded in memory but snapshot_events caps the returned slice. Doesn't actually solve REL-02 — RAM still grows. Reject. | |

**User's choice:** Deque + monotonic counter (Recommended)

### Q2: Migration approach for SSE route query param?

| Option | Description | Selected |
|--------|-------------|----------|
| Rename query param to `after_seq` | Frontend tracks `after_seq` instead of `after`. Templates need a one-line change. Clean break since this is single-user and there's no API contract to preserve. | ✓ |
| Keep `after` as the param name but treat it as seq | Same semantic change without renaming. Less honest to readers but zero template churn. | |
| Dual-mode: accept both for transition | Overengineered for solo use — reject. | |

**User's choice:** Rename query param to `after_seq` (Recommended)

### Q3: What if client's last seen seq is older than oldest event still in deque?

| Option | Description | Selected |
|--------|-------------|----------|
| Return everything currently in deque + warning event | Emit a synthetic `events.evicted` event ahead of the snapshot so the UI can show 'older events were dropped to save memory'. Honest about the gap. | ✓ |
| Return everything currently in deque silently | Just send what's there. UI shows a sudden jump in sequence numbers but no explicit warning. Simplest. | |
| Return 410 Gone, force reconnect from latest | Server tells client the old position is invalid. Heavier handling for solo-use. | |

**User's choice:** Return deque + synthetic events.evicted warning (Recommended)

---

## Configurability of caps (REL-01/02/03)

### Q1: Where do the three new caps live?

| Option | Description | Selected |
|--------|-------------|----------|
| Module-level constants | `_MAX_COMMENT_DEPTH=10` in api/comments.py, `_MAX_EVENTS_PER_JOB=1000` in web/runner.py, `_MAX_JOBS=50` in web/runner.py. Tests can monkeypatch. Zero config plumbing. | ✓ |
| All three in Config (TOML-exposed) | Adds three fields to Config and _DEFAULT_TOML. User can tune without code change. More plumbing through layers that don't currently see Config. | |
| Hybrid: depth in Config, memory caps as constants | Comment depth could plausibly need tuning per-story. Memory caps are operational tuning user shouldn't touch. Compromise. | |

**User's choice:** Module-level constants (Recommended)

### Q2: How does `_parse_one()` accept the depth cap?

| Option | Description | Selected |
|--------|-------------|----------|
| Default param reads constant | def _parse_one(raw, depth=0, max_depth=_MAX_COMMENT_DEPTH). Constant is the default; tests pass max_depth=3 to test the cap without monkeypatching. | ✓ |
| Constant only, no param | Tests monkeypatch the module constant. Cleaner signature but slightly worse for inline test setup. | |
| Param required, no default | Forces every caller to think about depth. Overkill — only one production caller. | |

**User's choice:** Default param reads constant (Recommended)

### Q3: REL-03 prune rule — what about running jobs?

| Option | Description | Selected |
|--------|-------------|----------|
| Skip running jobs when pruning | Iterate from oldest forward, evict only jobs in done/failed status until under cap. Running jobs are pinned. Cap may temporarily exceed if many running. | ✓ |
| Evict by age regardless of status | Strict cap. Pruning a running job orphans the JobRunner thread — it keeps writing events to a Job that's no longer in the manager. Bad. | |
| Refuse new job creation when cap reached | create() returns an error if cap full. User-facing failure when web UI is busy. Heavy-handed. | |

**User's choice:** Skip running jobs when pruning (Recommended)

---

## Render failure & comment truncation semantics (REL-04 + REL-01)

### Q1: How does archive_story signal 'all renders failed' to JobRunner?

| Option | Description | Selected |
|--------|-------------|----------|
| Raise RenderError after loop | New exception class in jobs.py. Run all 3 in try/except (collect per-format result), emit `story.done` with render_status dict, then raise RenderError if all 3 are failed. | ✓ |
| Raise on first failure | Short-circuit: first renderer to fail raises immediately. Loses partial-success information. | |
| Return status flag, no exception | archive_story returns a result dict; CLI/web code decides whether to fail the job. Changes the function signature; existing callers must adapt. | |

**User's choice:** Raise RenderError after loop (Recommended)

### Q2: What does comment truncation at depth >= 10 do?

| Option | Description | Selected |
|--------|-------------|----------|
| Drop replies past depth 10, keep parent | The depth-10 comment is preserved with replies=[]. Deeper replies are lost. We log once per truncation event noting the parent comment id and lost reply count. | ✓ |
| Drop the entire deep comment | Comments that would land at depth >10 are skipped entirely. Slightly more aggressive trim. | |
| Keep all + add truncation marker | Insert a synthetic Comment at depth 11 with body='[truncated due to depth limit]'. Visible to user but pollutes the data model. | |

**User's choice:** Drop replies past depth 10, keep parent (Recommended)

### Q3: Warning log frequency for comment-depth truncation?

| Option | Description | Selected |
|--------|-------------|----------|
| Per top-level comment if depth was hit | _parse_one tracks whether truncation happened during its recursive descent; emits one logger.warning per top-level comment whose subtree got truncated. Includes truncated count. | ✓ |
| Per truncation event | Every dropped reply logs. Could be hundreds per chapter on a deeply-nested thread. Noisy. | |
| Once per process via warnings module | warnings.warn with a deduplication category. User sees it once per session; loses count detail. | |

**User's choice:** Per top-level comment if depth was hit (Recommended)

### Q4: Where does the new RenderError exception class live?

| Option | Description | Selected |
|--------|-------------|----------|
| In jobs.py alongside ResolveError | ResolveError already lives there; RenderError is similar in scope (raised from archive_story). Single import for callers. | ✓ |
| New module wattpad_crawler/errors.py | Centralizes domain exceptions. Bigger refactor — out of phase scope. | |
| In render package __init__.py | Lives near the renderers. Logical but creates an unusual import for callers in jobs.py. | |

**User's choice:** In jobs.py alongside ResolveError (Recommended)

---

## Claude's Discretion

- Exact `nh3` API surface (`clean()` vs `Cleaner` instance)
- Truncation tracking mechanism (return tuple vs counter vs exception)
- Test fixture shapes for unit tests
- `events.evicted` event payload fields

## Deferred Ideas

- Comment body sanitization
- Per-format `nh3` allowlists for EPUB vs HTML
- Persist evicted events to disk
- Move caps to Config if multi-user/long-session needs ever appear
