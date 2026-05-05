# Phase 3: Circuit-breakers - Context

**Gathered:** 2026-05-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Two per-story circuit-breakers in `archive_story()` that abort loudly when:

1. **Extraction-empty (RES-01)** — 3 consecutive chapters where extracted `text < 100 chars` while raw `html > 5 KB` ⇒ Wattpad HTML structure likely changed; abort the story with a "selector likely changed" error before more empty parts get archived.
2. **HTTP-wall (RES-02)** — 5 consecutive non-200/non-404 HTTP responses (4xx excluding 404, 5xx) ⇒ sustained server-side wall (rate-limit, IP throttle, outage); abort with the recent error pattern.

Both breakers scoped to a single `archive_story()` call (RES-03). 404 mid-stream is treated specially: the part is marked `"gone"` (existing `PartStatus` literal value) and does **not** increment the HTTP-wall counter. Both breakers emit `breaker.opened` SSE progress events alongside the existing `auth.failed` / `part.failed` / `render.failed` vocabulary.

In scope: new `wattpad_crawler/circuit_breakers.py` (Breaker class, CircuitOpenError, four module-constant thresholds); per-part loop in `wattpad_crawler/jobs.py:archive_story()` (instantiate two breakers; observe extraction outcome + HTTP outcome; route 404 → `"gone"`; catch `CircuitOpenError` to emit `breaker.opened` then re-raise); minimal `httpx.HTTPStatusError`-shape inspection in the per-part except chain so the broad `except Exception` doesn't gobble HTTP status codes silently.

Out of scope: auto-resume / half-open state (RES-V2-01 deferred); persisted breaker state in manifest; in-story parallelism / `ThreadPoolExecutor` wiring (Phase 4 — but Phase 3 makes the breaker thread-safe today so Phase 4 doesn't need to revisit); streaming renders / VCR integration test (Phase 5); HTTP-wall counting `httpx.RequestError` transport errors (deliberately excluded — transport errors propagate as their own type per Phase 2 D-02; "HTTP-wall" means HTTP responses, not connection failures); UI banner styling for the `breaker.opened` event (existing `job.html` JSON-dump rendering is sufficient for v1).

</domain>

<decisions>
## Implementation Decisions

### Breaker abstraction (RES-01, RES-02, RES-03)

- **D-01:** **One `Breaker` class, two instances per `archive_story()` call.** Located in new module `wattpad_crawler/circuit_breakers.py`. Single class is generic enough to cover both kinds — distinguishing them by an instance attribute (e.g., `kind: Literal["extraction_empty", "http_wall"]`) and per-instance threshold. Two-class alternative (separate `ExtractionEmptyBreaker` / `HttpWallBreaker`) was rejected as more code without a behavioral split. Inline integer counters were rejected as untestable in isolation.
- **D-02:** **New module `wattpad_crawler/circuit_breakers.py`** mirrors the `wattpad_crawler/auth.py` precedent from Phase 2 (D-23). Houses: `Breaker` class, `CircuitOpenError(Exception)`, four `_lowercase` module constants — `_EXTRACTION_EMPTY_CONSECUTIVE = 3`, `_HTTP_WALL_CONSECUTIVE = 5`, `_TEXT_THRESHOLD = 100`, `_HTML_THRESHOLD = 5000`. Tests monkeypatch the constants for unit-testing trip thresholds (Phase 1 D-11 pattern). `jobs.py` imports `Breaker` and `CircuitOpenError`; constants stay private to the module.
- **D-03:** **`Breaker.record_failure(...)` raises `CircuitOpenError` itself** when the consecutive-count crosses its threshold. No separate `is_open()` check at the call site — the call simply doesn't return on trip. Mirrors the `RateLimitedClient.get()` raises-on-401 pattern (Phase 2 D-13). Counterpart `record_success()` resets the consecutive counter to 0 and clears the recent-tape. (Both methods take parameters specific to the breaker kind — see D-04 / D-08.)
- **D-04:** **Thread-safe today via `threading.Lock`.** A single `_lock` per Breaker instance guards the consecutive-count + recent-tape mutations. Phase 4 wires `ThreadPoolExecutor` for parallel chapter fetch — having the lock in place now (cheap, ~3 lines) avoids a Phase 4 reopen window where two workers race on the counter and never trip. Lock is acquired inside `record_failure` and `record_success`; `CircuitOpenError` is raised after lock release (so the raising thread doesn't hold the lock during exception unwind).

### CircuitOpenError shape (RES-03)

- **D-05:** **`CircuitOpenError(Exception)` lives in `circuit_breakers.py`** alongside the Breaker class. Inherits directly from `Exception` matching `ResolveError` / `RenderError` / `AuthError` precedent (Phase 1, Phase 2). Constructor: `CircuitOpenError(message: str, *, kind: Literal["extraction_empty", "http_wall"], threshold: int, count: int, recent: list[dict])`. The `recent` tape carries enough information for the planner-chosen `breaker.opened` payload (D-09) and for downstream forensics.
- **D-06:** **Per-kind error message text** (used as `job.error` / batch results dict value):
  - `extraction_empty` → `"selector likely changed: 3 consecutive chapters had <100 chars text from >5KB HTML"` — matches ROADMAP §"Phase 3" success criterion #1 wording verbatim so the verifier checks it literally.
  - `http_wall` → `"HTTP wall: 5 consecutive non-200/non-404 responses {status_codes}"` — includes the recent status codes inline (e.g., `[500, 503, 500, 429, 503]`) per ROADMAP §"Phase 3" success criterion #2 ("abort the story with the recent error pattern").
- **D-07:** **Counter semantics — reset on success.** A successful part (text ≥ 100 chars OR html ≤ 5 KB) resets `extraction_empty.consecutive_count` to 0 and clears its recent-tape. A non-200/non-404 HTTP response resets nothing for `extraction_empty`. A successful HTTP fetch (200) resets `http_wall.consecutive_count` to 0; a 404 resets nothing (and does not increment); a 4xx/5xx increments. Recent-tape on each breaker is bounded to its threshold size (3 / 5 entries) so trip-time tape == exact set that triggered.

### Extraction-empty part handling (RES-01)

- **D-08:** **Manifest status for an extraction-empty part is `body_text_failed`** — the existing `PartStatus` literal value (`wattpad_crawler/models.py:4`) was added for exactly this case. Using `body_text_failed` instead of generic `failed` preserves the "we got HTML but couldn't extract content" forensic signal — useful when Wattpad changes structure again. `pending_parts_for()` (`archive/state.py:150`) excludes `done/gone/private` only, so re-archive picks `body_text_failed` rows back up; no additional manifest filtering needed.
- **D-09:** **Write `raw_html.html` only on extraction-empty parts.** Persists the raw HTML response (unsanitized — same string `fetch_chapter_html()` returned) so the user can diff against future Wattpad layouts to confirm what changed. Skip JSON / text / comment files because they would contain near-empty placeholders that pollute the readable archive. **Implementation note for planner:** `archive/store.py:write_part_files()` currently writes all five file types in one call — Phase 3 either splits that helper into `write_raw_html(...)` + `write_chapter_payload(...)` or adds a `raw_html_only: bool = False` parameter; planner's choice based on which keeps callers cleaner.
- **D-10:** **`parts.last_error` for body_text_failed rows = `"extraction empty: text={n} chars, html={m} bytes"`** with the actual character counts substituted. Concrete numbers so the user (or future debugger) can confirm the heuristic from a `SELECT last_error FROM parts WHERE status='body_text_failed'`. Single-line, greppable.

### `breaker.opened` SSE event (RES-03)

- **D-11:** **Rich payload — include the recent failure pattern.** Event `kind = "breaker.opened"`. Payload schema:
  - For `extraction_empty`: `{"breaker": "extraction_empty", "threshold": 3, "count": 3, "recent": [{"part_id": ..., "ordinal": ..., "text_len": ..., "html_len": ...}, ×3]}`
  - For `http_wall`: `{"breaker": "http_wall", "threshold": 5, "count": 5, "recent": [{"status_code": ..., "url": ...}, ×5]}`
  - ROADMAP §"Phase 3" success criterion #3 says both events appear in SSE. ROADMAP §criterion #2 says "abort the story with the recent error pattern" — `recent` is that pattern.
- **D-12:** **`archive_story()` catches `CircuitOpenError`, emits `breaker.opened`, re-raises.** Symmetric to the Phase 2 `AuthFailedError` pattern (`02-CONTEXT.md` D-17). Layout in the per-part `try` block:
  1. `except AuthFailedError: ... raise` (existing — Phase 2)
  2. **NEW** `except CircuitOpenError as e: emit("breaker.opened", {"breaker": e.kind, "threshold": e.threshold, "count": e.count, "recent": e.recent}); raise` — placed BEFORE the broad `except Exception as e:` so the broad branch never gobbles a CircuitOpenError. Re-raise lets `JobRunner` mark the job `failed` via its existing top-level `except Exception` path.
  3. `except Exception as e:` (existing — broad catch, marks part failed, emits part.failed) — Phase 3 modifies this branch to inspect `httpx.HTTPStatusError` for status_code routing (404→gone & no breaker increment; other 4xx/5xx → http_wall.record_failure(...) which may raise CircuitOpenError that flies past this except into D-12 #2 above).
- **D-13:** **404 → `"gone"` mapping happens in `archive_story()`'s broad except branch**, not in `RateLimitedClient.get()`. Reason: the manifest write is a per-part concern that already lives in `archive_story`; pushing 404 detection into the client would split status-routing logic across two files. Implementation: in the broad except, `if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404: manifest.set_part_status(..., "gone", last_error="HTTP 404 — part removed"); emit("part.failed", {"part_id": ..., "status_code": 404, "reason": "gone"}); continue` — does NOT increment the http_wall counter.
- **D-14:** **HTTP-wall increments via `record_failure(status_code=..., url=...)` from the broad except.** Logic: `if isinstance(e, httpx.HTTPStatusError) and e.response.status_code != 404: http_wall.record_failure(status_code=e.response.status_code, url=e.request.url)`. The `record_failure` call may raise `CircuitOpenError` which is then caught by D-12 #2 — except: D-12 #2 sits BEFORE the broad except, so the breaker's `CircuitOpenError` flies past the broad except. Planner must verify the order: `except AuthFailedError → except CircuitOpenError → except Exception (which calls http_wall.record_failure that may raise CircuitOpenError up through this except's body)`. If `record_failure` raising inside the broad except's body bypasses Python's except-matching (it does — exceptions raised inside an except handler propagate up), the order is correct as described. Documenting because it's subtle.

### HTTP-wall scope (RES-02 — locked default)

- **D-15:** **HTTP-wall counts only HTTP 4xx (excluding 404) and 5xx responses** per the literal REQUIREMENTS RES-02 wording ("counts consecutive 4xx (excluding 404) and 5xx responses"). **`httpx.RequestError` transport errors (timeout, connection refused, DNS failure) are explicitly NOT counted** — they propagate as their own type per Phase 2 D-02 ("don't conflate transport with auth"; same principle: don't conflate transport with HTTP-wall). User selected this default by not picking the "HTTP-wall scope" gray area for discussion. CONCERNS.md mentioned consecutive timeouts as a related concern — that lives in V2 if it ever materializes (RES-V2-01 territory).
- **D-16:** **429 retries inside `RateLimitedClient.get()` continue as today.** If `get()` exhausts `max_attempts` (default 5) and the final outcome is still a 429, `raise_for_status()` raises `HTTPStatusError(429)`. That counts as ONE http_wall increment (the final outcome), not five (one per retry). The retry path is invisible to the breaker because retries happen inside `get()` before the response is returned to `archive_story`.

### Claude's Discretion

- **`recent`-tape data structure inside Breaker** — a `collections.deque(maxlen=threshold)` is the obvious fit (auto-evicts on overflow during pre-trip records, exact-size on trip). Planner picks whichever stays simplest.
- **Splitting `write_part_files()` for raw-HTML-only writes (D-09)** — `raw_html_only=True` flag vs splitting into two helpers; planner picks based on which keeps `archive_story` and `store.py` callers cleaner.
- **Whether `http_wall.record_failure` accepts the full `httpx.HTTPStatusError` or just `(status_code, url)` ints/strings** — both work; the latter keeps Breaker free of httpx imports (cleaner for unit tests with synthetic inputs); planner's call.
- **Test fixture shapes** — unit tests for `Breaker` in isolation (record N-1 failures → not open; record Nth → CircuitOpenError raised with recent tape of length N); integration shape via `archive_story` with monkeypatched `parse_chapter` returning `ChapterContent(text="", paragraphs=[], images=[])` for ≥3 consecutive parts and a synthetic raw_html ≥5KB; integration shape via `JobDeps`-injected `fetch_chapter_html` that raises `httpx.HTTPStatusError(503)` for 5 consecutive parts. Planner picks fixture shapes; tests must monkeypatch the four module constants down (`_EXTRACTION_EMPTY_CONSECUTIVE=2` for tests, etc.) to keep test stories small.
- **Whether to log a `logger.warning` on each `record_failure` increment** before the trip — probably yes (loud failures principle), but the log shape is planner's call. Tests for breaker tripping must not assert on the warning text.
- **Boundary-of-empty heuristic edge cases** — `text == ""` (truly empty) AND `html < 5KB` (genuine short chapter) is a no-trip case; counter does NOT reset (the chapter is "successful enough" — record a part.done). Confirm by reading the heuristic literally: trip increment requires BOTH `text < 100` AND `html > 5 KB`; either side false ⇒ a regular success path.

### Folded Todos

None — `gsd-tools todo match-phase 3` returned zero matches.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level

- `.planning/REQUIREMENTS.md` §"Resilience" — RES-01 (extraction-empty: 100-char / 5-KB / 3-consecutive), RES-02 (HTTP-wall: 4xx-excl-404 + 5xx / 5-consecutive), RES-03 (per-story scope + `breaker.opened` events). Thresholds and 404 special-case are locked here, not negotiable.
- `.planning/REQUIREMENTS.md` §"Out of Scope" — explicitly excludes `pybreaker` / `circuitbreaker` libraries; the small inline implementation is the chosen path.
- `.planning/REQUIREMENTS.md` §"Future Resilience" — RES-V2-01 (auto-resume / half-open) explicitly deferred; Phase 3 just opens-and-aborts.
- `.planning/PROJECT.md` §Constraints — single-process, Windows-first, no new deps. circuit_breakers.py uses stdlib only (`threading`, `collections.deque`, `typing`).
- `.planning/PROJECT.md` §"Active" — bullets 3 & 4 frame the user-visible behavior ("circuit-breaker on chapter extraction" + "circuit-breaker on rate-limit / auth walls"). PROJECT.md §"Key Decisions" row 3 records the rationale: "circuit-breaker bounds the blast radius and gives a loud signal when Wattpad changes structure."
- `.planning/ROADMAP.md` §"Phase 3: Circuit-breakers" — Goal statement and three success criteria; verifier checks these literally. Success criterion #1 wording ("selector likely changed") drives D-06 message text.

### Phase 1 / Phase 2 carry-forward

- `.planning/phases/01-local-hardening-fixes/01-CONTEXT.md` §Decisions §"Cap configurability" — establishes module-constant pattern (`_lowercase` leading-underscore) which D-02 follows. Phase 1 also delivers the nh3 sanitization that makes empty `content.text` meaningful (RES-01 prerequisite — STATE.md "nh3 chosen over bleach; must land Phase 1 before extraction-empty circuit-breaker in Phase 3").
- `.planning/phases/02-auth-hardening/02-CONTEXT.md` §Decisions D-13..D-17 — `RateLimitedClient.get()` already short-circuits on 401/403/400-PermissionDenied → `AuthFailedError` BEFORE any other status check. Auth failures NEVER reach the HTTP-wall breaker, by design. The `auth.failed` SSE event vocabulary (D-17) is the precedent the new `breaker.opened` event follows.
- `.planning/phases/02-auth-hardening/02-CONTEXT.md` §Decisions §"Mid-Job Detection" D-16 — establishes the `except AuthFailedError: emit; raise` precedent in `archive_story()` per-part block; D-12 of this phase mirrors that pattern for `CircuitOpenError`.

### Codebase intel

- `.planning/codebase/CONCERNS.md` §"Brittle CSS selector for chapter content extraction" (lines 20-28) — origin of RES-01; explicit recommendation "if 3+ consecutive chapters return zero paragraphs, pause and error loudly".
- `.planning/codebase/CONCERNS.md` §"Undocumented rate-limit detection gaps" (lines 30-38) — origin of RES-02; recommendation "if 10+ consecutive requests timeout, auto-pause" — note the "timeout" framing; deliberately scoped down to HTTP responses for v1 (D-15) per REQUIREMENTS-as-written.
- `.planning/codebase/CONCERNS.md` §"Part body text extraction failure is not detected" (lines 212-219) — origin of the 100-char / 5-KB heuristic.
- `.planning/codebase/CONVENTIONS.md` §"Naming Patterns", §"Type Hints", §"Error Handling" — `_lowercase` module constants; `T | None` pipe-syntax unions; custom exceptions inherit directly from `Exception`. All applied throughout D-01..D-16.
- `.planning/codebase/STRUCTURE.md` §"Where to Add New Code" — top-level `wattpad_crawler/` package houses cross-cutting concerns; `circuit_breakers.py` slots in alongside `auth.py` and `client.py`.
- `.planning/codebase/ARCHITECTURE.md` — layered architecture; `archive_story()` is the single entry point shared by CLI and JobRunner. Per-story scope (RES-03) ⇒ Breaker instances live in archive_story's stack frame.

### Files to edit (verified during scout)

- `wattpad_crawler/circuit_breakers.py` — **NEW** module: `Breaker` class, `CircuitOpenError`, four `_lowercase` constants (`_EXTRACTION_EMPTY_CONSECUTIVE = 3`, `_HTTP_WALL_CONSECUTIVE = 5`, `_TEXT_THRESHOLD = 100`, `_HTML_THRESHOLD = 5000`).
- `wattpad_crawler/jobs.py:66-207` — `archive_story()`: import `Breaker, CircuitOpenError`; instantiate `extraction_empty` and `http_wall` Breakers at the top of the function (per-call scope per RES-03); inside the per-part try-block use the pattern: extract → check 100-char/5KB heuristic → if triggered: `extraction_empty.record_failure(part_id, ordinal, text_len, html_len)` BEFORE writing files (D-09); else: `extraction_empty.record_success()` and continue normal flow; in the per-part except chain insert `except CircuitOpenError` (D-12 #2) BEFORE the broad `except Exception`; modify the broad except to inspect `httpx.HTTPStatusError` for 404→gone (D-13) vs other 4xx/5xx → `http_wall.record_failure` (D-14).
- `wattpad_crawler/archive/store.py` — `write_part_files()` either gains a `raw_html_only: bool = False` parameter or is split (Claude's Discretion) so `archive_story` can persist raw HTML alone for `body_text_failed` parts (D-09).
- (No edits expected to `client.py`, `auth.py`, `web/routes.py`, `web/runner.py`, or any template — this phase is contained to `circuit_breakers.py` (new) + `jobs.py` (modify) + a small touch on `store.py`.)

### Test fixture sites

- New `tests/unit/test_circuit_breakers.py` — `Breaker` in isolation: 2-of-3 records do not trip; 3rd record raises `CircuitOpenError` carrying recent tape of length 3 with the kind-specific shape (D-11); `record_success()` resets the counter mid-stream; per-instance threading.Lock holds under a 2-thread race that interleaves `record_failure` calls and exactly one of them raises (D-04).
- `tests/unit/test_jobs.py` — `archive_story()` end-to-end: monkeypatch `parse_chapter` to return `ChapterContent(text="", paragraphs=[], images=[])` for 3 consecutive parts with a fake `fetch_chapter_html` returning ≥5KB raw HTML — assert `breaker.opened` SSE event payload matches D-11 schema, `JobRunner` ends `failed`, `parts.status == 'body_text_failed'` for the three triggering parts, `parts.last_error` matches D-10 format (D-08, D-10, D-11). Symmetric test for HTTP-wall: `JobDeps`-injected `fetch_chapter_html` that raises `httpx.HTTPStatusError(503)` for 5 consecutive parts (use 5x distinct URLs in `recent`); a 6th-from-the-end test injecting one 404 mid-stream confirms the http_wall counter does NOT increment and the part status is `gone` (D-13).
- `tests/unit/test_jobs.py` — boundary tests: `text_len = 99`, `html_len = 5001` triggers; `text_len = 100`, `html_len = 5001` does NOT trigger; `text_len = 99`, `html_len = 5000` does NOT trigger (D-10 / Claude's Discretion edge cases).

### External (researcher to fetch / verify)

- No external docs needed — circuit-breaker logic is pure stdlib + httpx exception inspection. Phase 1 nh3 sanitization is the only upstream dependency and is already validated.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`wattpad_crawler/auth.py` precedent** — D-23 in Phase 2 created a small standalone module with one class + custom exception + lowercase module constants. `circuit_breakers.py` mirrors this shape exactly (D-02). Same `from wattpad_crawler.X import Y` absolute-import style.
- **`PartStatus = Literal["pending", "in_progress", "done", "failed", "body_text_failed", "gone", "private"]`** in `wattpad_crawler/models.py:4` — `body_text_failed` and `gone` are pre-existing literal values. D-08 uses `body_text_failed`; D-13 uses `gone`. No `PartStatus` literal change required, no manifest schema migration.
- **`Manifest.set_part_status(story_id, part_id, status, *, body_hash=None, last_error=None)`** at `archive/state.py:110` — already accepts `last_error` keyword. D-10 (`extraction empty: text=N chars, html=M bytes`) plugs straight in. `pending_parts_for()` (`archive/state.py:150`) excludes `done/gone/private` — `body_text_failed` rows correctly retry on next archive run, no filter change needed.
- **`emit("kind", {"data": ...})` callback in `archive_story()`** — already used for `auth.failed`, `part.failed`, `render.failed`, `events.evicted`. D-11/D-12 add `breaker.opened` to that vocabulary, no new plumbing.
- **`@dataclass` precedent** — `JobDeps`, `Job`, `ProgressEvent` all use plain `@dataclass`. Breaker can use `@dataclass` with a `__post_init__` for the lock, or a plain `__init__`; both fit the codebase. `frozen=True` is wrong here (mutable counter).
- **`logger = logging.getLogger(__name__)` pattern** — `circuit_breakers.py` follows the same pattern. `logger.warning` for each `record_failure` increment before trip (Claude's Discretion).
- **`threading.Lock` precedent** — `Job._lock`, `TokenBucket._lock` (`client.py:32`) — established pattern; D-04 follows. Acquire → mutate → release before raise.

### Established Patterns

- **Custom exceptions inherit from `Exception` directly** — `class CircuitOpenError(Exception)` matches `ResolveError`, `RenderError`, `AuthError` (Phase 1, Phase 2).
- **Per-part except chain order matters** — Phase 2 D-16 added `except AuthFailedError: ... raise` BEFORE the broad `except Exception`. Phase 3 D-12 adds `except CircuitOpenError: ... raise` in that same band. Order: AuthFailedError → CircuitOpenError → Exception (broad).
- **Module constants over Config for caps** — Phase 1 D-11 established that the `_MAX_*` thresholds are not TOML-exposed; tests monkeypatch the constants. Phase 3 D-02 follows: tests for D-08/D-11 will monkeypatch `_EXTRACTION_EMPTY_CONSECUTIVE` and `_HTML_THRESHOLD` to small values so test stories stay small.
- **JobRunner already catches uncaught Exception → `set_failed`** (`web/runner.py`) — `CircuitOpenError` flows through unchanged. No JobRunner changes.
- **`archive_many()` per-story `except Exception` records `failed: {e}` and continues** (`jobs.py:268-275`) — when Story A trips a breaker mid-batch, the next story still attempts. Acceptable for v1 (mirrors the Phase 2 D-18 NOTE comment about `AuthFailedError` mid-batch behavior).
- **`logger.warning` for recoverable issues, `logger.exception` for caught-and-handled exceptions** — Phase 1 / Phase 2 carry-forward. Use `warning` for the per-record-failure log; the broad except already uses `logger.exception(...)` and that stays.

### Integration Points

- **`archive_story()` per-part try block (`jobs.py:114-173`)** — single function rewrite. After Phase 3 the body grows by ~25 lines: two Breaker instantiations at the top; ~6 lines of extraction-empty heuristic + record_success/record_failure routing inside the try; one new `except CircuitOpenError` clause; ~8 lines of `httpx.HTTPStatusError` inspection inside the broad except. No new function-level signature changes; `archive_many()` and JobRunner are untouched.
- **`store.py:write_part_files()`** — touched once for D-09 (raw_html-only path). Either grows a kwarg or splits into two helpers.
- **No client.py changes** — D-13 keeps 404 routing in `archive_story`'s broad except; D-15/D-16 don't change retry logic; the existing `raise_for_status()` propagation is what surfaces 4xx/5xx as `httpx.HTTPStatusError`.
- **No template changes** — the existing `job.html` (`web/templates/job.html:40`) renders SSE events as `<code>{kind}</code> {JSON.stringify(data)}` — `breaker.opened` events display fine alongside the existing `auth.failed` / `part.failed` / `render.failed` events. UI styling for the breaker banner is deferred (out of scope this phase).
- **Web `JobRunner._run` already catches Exception → `set_failed(str(e))`** (Phase 1 / Phase 2 carry-forward). The `CircuitOpenError` message text (D-06) is what lands in `job.error` and gets displayed in the existing `job.html` "Error:" card.

</code_context>

<specifics>
## Specific Ideas

- **Loud failure philosophy carries forward** — Phase 2's "loud-and-immediate over polite-and-deferred" framing applies verbatim: a circuit breaker that opens does NOT half-open, does NOT auto-resume, does NOT silently mark the story partial. It raises, the job ends `failed`, the user sees `breaker.opened` in SSE plus a kind-specific error message in the failed-job banner. Restart is manual.
- **Per-story scope is a deliberate simplification** — RES-03 says "one breaker pair per `archive_story()` call". A library-archive of 100 stories that hits the breaker on story 7 fails story 7 only and continues to story 8 (existing `archive_many` behavior). If story 8 also trips, that's the same Wattpad-broke-its-HTML signal; the user reads the SSE stream and stops the job. Pool-wide breaker state is rejected as overengineered for solo use (mirrors `pybreaker` rejection).
- **Order in the per-part try/except band is subtle but locked** — `AuthFailedError` (Phase 2) → `CircuitOpenError` (Phase 3 — NEW) → broad `Exception` (modified to inspect `httpx.HTTPStatusError`). Documenting because D-14's `record_failure` raise-from-inside-except is the trickiest control flow in the phase.
- **"HTTP-wall" excludes transport errors by design** — D-15 mirrors Phase 2 D-02 ("don't conflate transport with auth") for "don't conflate transport with HTTP-wall". A wall of timeouts is a different signal — if it ever materializes, REQUIREMENTS Future-Resilience is the home. v1 keeps the spec literal.
- **Re-use, don't re-invent** — circuit_breakers.py copies auth.py's shape; the per-part except chain copies Phase 2's `AuthFailedError` precedent; thread-safety copies `TokenBucket`'s `Lock` pattern. New code is minimal: ~70 lines in `circuit_breakers.py` + ~25 lines added to `archive_story` + a 1-arg / 1-helper touch on `store.py`.

</specifics>

<deferred>
## Deferred Ideas

- **Auto-resume / half-open state** — REQUIREMENTS RES-V2-01 explicitly defers; manual restart is the v1 UX. The `body_text_failed` and `failed` rows in the manifest let the next archive run pick up where the previous one stopped, which is good enough for solo use.
- **Persisted breaker state across runs** — REQUIREMENTS RES-V2-02 territory; in-memory per-call scope is sufficient.
- **HTTP-wall counting `httpx.RequestError` (transport errors)** — explicitly excluded in D-15; revisit only if a real-world wall-of-timeouts is observed without an accompanying HTTP-response wall.
- **Pool-wide / multi-story breaker state** — rejected; per-story scope per RES-03. A future phase that adds inter-story rate-coordination could revisit.
- **UI banner / styling for `breaker.opened`** — current `job.html` renders the event as raw JSON, which is enough for v1 solo use. A "Visual Polish" milestone could promote it to a coloured banner alongside `auth.failed`.
- **Lifting `_EXTRACTION_EMPTY_CONSECUTIVE` etc into TOML config** — explicitly rejected (Phase 1 D-11 pattern); revisit only if a real tuning need surfaces.
- **Recording how often each breaker fires across runs (analytics)** — overkill for solo use; logs already capture each `record_failure` increment.
- **Migrating off `data-p-id` selector** — explicitly out of scope (REQUIREMENTS §"Out of Scope": "circuit-breaker bounds blast radius instead"). The breaker is the v1 mitigation.

</deferred>

---

*Phase: 03-circuit-breakers*
*Context gathered: 2026-05-05*
