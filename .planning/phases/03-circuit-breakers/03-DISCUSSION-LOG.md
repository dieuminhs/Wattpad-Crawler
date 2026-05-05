# Phase 3: Circuit-breakers - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-05
**Phase:** 03-circuit-breakers
**Areas discussed:** Breaker abstraction shape, Extraction-empty part status, breaker.opened payload

---

## Gray Areas Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Breaker abstraction shape | One Breaker class with two instances vs two specific classes vs inline counters; thread-safety planning for Phase 4 | ✓ |
| HTTP-wall scope | Strict (HTTP 4xx-excl-404 + 5xx only) vs broader (also `httpx.RequestError`); CONCERNS.md cited consecutive timeouts | |
| Extraction-empty part status | `body_text_failed` (existing PartStatus literal) vs generic `failed` | ✓ |
| `breaker.opened` payload | Minimal (kind, threshold, count) vs rich (kind + count + recent failure pattern) | ✓ |

**Notes:** HTTP-wall scope unselected — locked to strict spec read (4xx-excl-404 + 5xx HTTP responses only) per literal REQUIREMENTS RES-02 wording. `httpx.RequestError` propagates as its own type (Phase 2 D-02 precedent for not conflating transport with HTTP semantics).

---

## Breaker abstraction shape

### Q1: What shape should the circuit-breaker take?

| Option | Description | Selected |
|--------|-------------|----------|
| One Breaker class, two instances | Single small class with record_failure(kind), record_success(kind), is_open(kind), threshold, recent failure tape. archive_story() instantiates two: extraction_empty and http_wall. Generic + testable. | ✓ |
| Two specific classes | ExtractionEmptyBreaker and HttpWallBreaker as separate small classes — each owns its own threshold and tape semantics | |
| Inline integer counters | Just two ints + two lists in archive_story body, no class | |

**User's choice:** One Breaker class, two instances
**Notes:** Recommended option; aligns with PROJECT.md "explicit, testable code over heavy abstractions" while keeping the breaker testable in isolation.

### Q2: Where should the Breaker class and threshold constants live?

| Option | Description | Selected |
|--------|-------------|----------|
| New `wattpad_crawler/circuit_breakers.py` | Standalone module; mirrors auth.py precedent (Phase 2 D-23). Imported by jobs.py | ✓ |
| Inline in jobs.py | Class + constants alongside RenderError/ResolveError | |
| Inline class, constants in config | Caps in TOML config (rejected by Phase 1 D-11 — listed for symmetry) | |

**User's choice:** New `wattpad_crawler/circuit_breakers.py`
**Notes:** Keeps jobs.py focused on the archive pipeline; symmetric to Phase 2's auth.py module split.

### Q3: How should the breaker actually trip and abort?

| Option | Description | Selected |
|--------|-------------|----------|
| Breaker raises from `record_failure()` | When record_failure(kind) crosses threshold, the method raises CircuitOpenError(kind, count, recent) directly. archive_story doesn't need a separate is_open() check | ✓ |
| archive_story checks `is_open()` after each record | record_failure(kind) just bumps; archive_story calls is_open(kind) and raises CircuitOpenError itself | |

**User's choice:** Breaker raises from `record_failure()`
**Notes:** Mirrors RateLimitedClient.get() raise-on-401 pattern (Phase 2 D-13); concise call site.

### Q4: Should the Breaker be thread-safe today or punt to Phase 4?

| Option | Description | Selected |
|--------|-------------|----------|
| Thread-safe now (`threading.Lock`) | Add a Lock around counter/tape mutations in Phase 3. Phase 4 wires ThreadPoolExecutor — having the lock already in place avoids a reopen window | ✓ |
| Not thread-safe — Phase 4 handles it | Document "single-threaded; Phase 4 must wrap or replace." | |

**User's choice:** Thread-safe now (`threading.Lock`)
**Notes:** ~3 lines of cost in Phase 3 saves a Phase 4 revisit; matches TokenBucket._lock precedent in client.py.

---

## Extraction-empty part status

### Q1: When extraction-empty triggers (text<100 + html>5KB), what manifest status does that part get?

| Option | Description | Selected |
|--------|-------------|----------|
| `body_text_failed` | Existing PartStatus literal value designed for this exact case. Distinguishes "got HTML but couldn't extract" from "couldn't fetch / network died" | ✓ |
| `failed` | Lump with HTTP/auth failures — simpler but loses the "extraction shape changed" signal | |

**User's choice:** `body_text_failed`
**Notes:** Uses the existing `models.py:4` literal value that was added for exactly this case. Preserves forensic distinction.

### Q2: When extraction-empty triggers on a part, do we still write part files to disk?

| Option | Description | Selected |
|--------|-------------|----------|
| Write `raw_html.html` only | Persist raw HTML response (so user can diff against future Wattpad layouts) but skip JSON/text/comments | ✓ |
| Write nothing | Bail before write_part_files(); manifest gets the status, filesystem stays clean | |
| Write all files normally | Behave as if the part succeeded; the empty text/JSON is the evidence | |

**User's choice:** Write `raw_html.html` only
**Notes:** Forensic capture without polluting the readable archive. Implementation detail (split helper vs `raw_html_only=True` kwarg) is Claude's Discretion.

### Q3: What should be written to the parts.last_error column for extraction-empty rows?

| Option | Description | Selected |
|--------|-------------|----------|
| Structured: `'extraction empty: text=N chars, html=M bytes'` | Concrete numbers; greppable; self-explanatory | ✓ |
| Generic: `'extraction empty (selector likely changed)'` | Same message users see when the breaker opens | |
| Leave NULL | Status alone is enough | |

**User's choice:** Structured numeric format
**Notes:** A future debugger can confirm the heuristic from a SQL query without reading code.

---

## breaker.opened payload

### Q1: How rich should the breaker.opened SSE payload be?

| Option | Description | Selected |
|--------|-------------|----------|
| Rich: include recent failure pattern | Payload includes recent tape (3× extraction-empty entries OR 5× http_wall entries) | ✓ |
| Minimal: kind + threshold + count | Just metadata; UI consults logs/manifest for details | |

**User's choice:** Rich payload
**Notes:** Matches ROADMAP §"Phase 3" success criterion #2 wording ("abort the story with the recent error pattern").

### Q2: What goes in the CircuitOpenError message string?

| Option | Description | Selected |
|--------|-------------|----------|
| Distinct per breaker kind | extraction_empty → "selector likely changed: 3 consecutive chapters had <100 chars text from >5KB HTML"; http_wall → "HTTP wall: 5 consecutive non-200/non-404 responses [statuses]" | ✓ |
| Generic + payload | "circuit breaker opened: <kind>" — details only in event payload | |

**User's choice:** Distinct per breaker kind
**Notes:** Failed-job error column is more useful by itself; matches ROADMAP success-criterion wording verbatim so the verifier can check it literally.

### Q3: Where in archive_story() does the breaker.opened event fire?

| Option | Description | Selected |
|--------|-------------|----------|
| `archive_story` catches `CircuitOpenError`, emits, re-raises | Symmetric to AuthFailedError pattern (Phase 2 D-17). Per-part `except CircuitOpenError` block emits then re-raises so JobRunner marks job failed | ✓ |
| Breaker.record_failure emits before raising | Pass emit callback into Breaker constructor; breaker emits itself, then raises | |

**User's choice:** archive_story catches and emits
**Notes:** Keeps Breaker free of the SSE event-emit shape; cleaner separation of concerns.

---

## Claude's Discretion

Areas where the user explicitly deferred to Claude during planning/implementation:

- Recent-tape data structure inside Breaker (`collections.deque(maxlen=threshold)` is the obvious fit)
- `write_part_files()` API change for raw-HTML-only writes (kwarg vs split helper)
- Whether `http_wall.record_failure` accepts a full `httpx.HTTPStatusError` or just `(status_code, url)` ints/strings
- Test fixture shapes (specifically how to drive 5 consecutive 503s through the JobDeps-injected fetcher)
- Whether to log `logger.warning` per `record_failure` increment before trip (probably yes per loud-failure principle)
- Boundary edge cases: `text == ""` AND `html < 5KB` is a no-trip case — confirm by reading the heuristic literally (both conditions must hold)

## Deferred Ideas

Captured during discussion as out-of-scope or future-milestone material:

- Auto-resume / half-open state (RES-V2-01 — REQUIREMENTS already defers)
- Persisted breaker state across runs (RES-V2-02 territory)
- HTTP-wall counting `httpx.RequestError` transport errors (D-15 explicit exclusion; revisit if real-world wall-of-timeouts is observed)
- Pool-wide / multi-story breaker state (rejected by RES-03 per-story scope)
- UI banner / styling for `breaker.opened` (existing JSON-dump rendering is enough for v1)
- Lifting threshold constants into TOML config (rejected by Phase 1 D-11 pattern)
- Analytics on how often each breaker fires across runs
- Migrating off `data-p-id` selector entirely (REQUIREMENTS §"Out of Scope" — circuit-breaker is the v1 mitigation)
