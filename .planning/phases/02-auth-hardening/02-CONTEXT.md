# Phase 2: Auth hardening - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning

<domain>
## Phase Boundary

A new `wattpad_crawler/auth.py` module that catches dead-cookie failures at three points so a stale cookie produces an immediate, loud error rather than hours of empty-chapter "successes":

1. **Pre-archive validation** — CLI archive commands and `/setup` POST probe Wattpad before doing any work (AUTH-01, AUTH-02, AUTH-03)
2. **Mid-job detection** — `RateLimitedClient.get()` short-circuits on 401/403 so a cookie that goes bad mid-archive aborts the job loudly instead of silently failing every chapter (AUTH-04)
3. **Atomic cookie persistence** — `/setup` POST writes `_config.toml` via tmp + `os.replace()` so a crash during a write never leaves a zero-byte or partial config (AUTH-05)

In scope: new `wattpad_crawler/auth.py` (validate_cookie, AuthError, AuthFailedError); CLI hook in archive/library/list/url branches in `cli.py`; updates to `web/routes.py` setup_post + `_save_cookie()`; updates to `client.py:RateLimitedClient.get()`; updates to `archive_story()` re-raise behavior + new `auth.failed` progress event; setup.html template addition for error banner + masked attempted cookie.

Out of scope: cookie refresh / re-login automation (AUTH-V2-01); persisting auth status to manifest; OS-keyring credential storage; circuit-breakers (Phase 3); in-story parallelism (Phase 4); streaming renders / integration test (Phase 5); switching to async httpx; cover-fetch auth handling (covers tolerate failures already per Phase 1).

</domain>

<decisions>
## Implementation Decisions

### Probe & Error Taxonomy (AUTH-01)

- **D-01:** `validate_cookie(client: RateLimitedClient) -> None` probes a **single canonical endpoint** — researcher confirms the exact path (default candidate: `GET https://www.wattpad.com/api/v3/users/me`, flagged as "verify during Phase 2 implementation" in STATE.md). One probe site means one place to update if Wattpad changes endpoints.
- **D-02:** Auth failure signals are **strict**: HTTP 401, HTTP 403, and 3xx redirects whose `Location` header points at `/login` (or similar). Anything else — network errors (`httpx.RequestError`), timeouts, 5xx — propagates as its own exception type. Don't conflate transport with auth.
- **D-03:** **Two error classes**, both in `wattpad_crawler/auth.py`:
  - `class AuthError(Exception)` — raised by `validate_cookie()` at startup / `/setup` POST. Base class.
  - `class AuthFailedError(AuthError)` — raised by `RateLimitedClient.get()` mid-job on 401/403. Subclass so callers can `except AuthError` (catches both) or `except AuthFailedError` (catches only the mid-job variant). Matches REQUIREMENTS.md AUTH-01 / AUTH-04 wording.
- **D-04:** `validate_cookie()` makes the probe via the **existing `RateLimitedClient` with `max_attempts=1`**. Reuses the configured cookie / user-agent / token bucket; doesn't burn retries on a probe; no duplicate httpx setup. One token consumed per probe — negligible.

### CLI Validation Policy (AUTH-02)

- **D-05:** Validation runs in a **helper function** (e.g., `_require_auth(cfg, client)` defined in `cli.py` or imported from `auth.py`) called as the **first line of each archive branch**: `story`, `url`, `library`, `list`. Easy to add/remove per command, easy to test in isolation. No decorator pattern, no pre-dispatch allowlist.
- **D-06:** **`status` skips validation** alongside `serve` (REQUIREMENTS.md AUTH-02 already exempts serve). `status` reads only local `_state.sqlite` — no Wattpad API calls; validating would add a network dependency to a local read.
- **D-07:** **No `--skip-auth-check` opt-out flag.** Validation always runs for archive commands. Keeps CLI surface small. Developer running locally can edit `auth.py` if they truly need to bypass (covers the "Wattpad down, want to try anyway" rare case).
- **D-08:** **CLI failure UX:** validation failure prints to stderr `"AuthError: cookie rejected by Wattpad (HTTP {code}). Update your cookie via /setup or edit {output_dir}/_config.toml."` and exits with `sys.exit(2)` (convention: 2 = misuse / config error). No traceback noise. Achieved by catching `AuthError` in `main()` around the dispatch block and emitting the formatted message before exit.

### /setup Error UX (AUTH-03)

- **D-09:** **Re-render `setup.html` in-place** with a 400 status when validation fails — `templates.TemplateResponse(setup.html, ..., status_code=400)`. Mirrors the inline-form pattern users expect; redirect-with-flash would lose the cookie value typed in.
- **D-10:** **Three error categories** distinguished in the rendered page:
  - `auth` (401/403 / redirect-to-login) — banner: "Cookie rejected by Wattpad. Re-copy from your browser."
  - `network` (`httpx.RequestError`) — banner: "Could not reach Wattpad. Check connection then retry."
  - `unexpected` (anything else) — banner: "Validation failed: {repr(error)}"
  Implementation: `setup_post` catches `AuthError`, `httpx.RequestError`, then bare `Exception` separately and sets `error_kind` + `error_message` context vars consumed by the template.
- **D-11:** **Show the rejected cookie back, masked.** On error, pass `attempted_cookie_masked = _mask(submitted_value)` to the template alongside the existing `current_cookie_masked`. User sees `"abcd…4321"` and can verify they pasted the right one. Saved `_config.toml` is unchanged (per AUTH-03), so `current_cookie_masked` reflects the last *successfully saved* cookie if any.
- **D-12:** **Validation runs BEFORE `_save_cookie()`.** `setup_post` flow: receive form → call `validate_cookie()` with a transient client built around the submitted cookie → on success, call `_save_cookie()` (now atomic per D-19) and reload config → 303 redirect to `/setup?saved=1`. On failure, do **not** touch `_config.toml` (success criterion #2). Implementation note: building the transient client requires a temporary `Config` clone with the submitted cookie — Claude's Discretion how to assemble it cleanly.

### Mid-Job Detection & Propagation (AUTH-04)

- **D-13:** **401/403 detection lives in `RateLimitedClient.get()`** — insert a check `if resp.status_code in (401, 403): raise AuthFailedError(...)` BEFORE the existing 5xx / 429 retry branches and BEFORE `resp.raise_for_status()`. Universal coverage — every API call (story, parts, comments, user, library) is protected by one branch.
- **D-14:** **Fail on the FIRST 401/403 response.** Do not retry, do not backoff, do not consume more than one attempt. 401/403 are deterministic: cookies don't "flap." Burning 5 attempts × 100 chapters wastes minutes and tokens.
- **D-15:** **`AuthFailedError` payload** carries `status_code: int` (401 or 403) and `url: str` (the request URL that triggered it). Constructor: `AuthFailedError(f"Wattpad returned HTTP {code} for {url} — cookie likely expired", status_code=code, url=url)`. Useful for the `auth.failed` progress event payload (D-17) and for logs.
- **D-16:** **`archive_story()` propagation:** the existing per-part `try/except Exception` block must NOT swallow `AuthFailedError`. Re-raise at the top of the except handler. Two acceptable shapes (planner's choice):
  - Add `except AuthFailedError: raise` BEFORE the broad `except Exception as e:` block (preferred — more idiomatic Python)
  - First line of the broad `except Exception as e:` block: `if isinstance(e, AuthFailedError): raise`
  Either way: the error flies out of `archive_story()` → `JobRunner` catches it via its existing top-level `except Exception` → marks job `failed` with the message. Covers the success criterion that the job ends `failed`, not `done`.
- **D-17:** **Emit `auth.failed` progress event** before re-raising in `archive_story()`. Payload: `{"part_id": part.part_id, "status_code": e.status_code, "url": e.url, "message": str(e)}`. SSE consumers see an explicit auth signal in the stream **before** the `__status__: failed` sentinel — UI can highlight "cookie went bad mid-job" specifically rather than showing a generic failure. The `auth.failed` event lives alongside the existing `part.failed` / `render.failed` / `breaker.opened` (Phase 3) vocabulary.
- **D-18:** **`fetch_story()` and pre-loop calls also covered** — because detection is in `RateLimitedClient.get()` (D-13), a 401/403 returned to the initial `deps.fetch_story(client, story_id)` call (line 78 of jobs.py) raises `AuthFailedError` directly — no special handling needed at that site; it propagates straight to JobRunner. Same for `fetch_library()` / `fetch_list_story_ids()` in CLI batch commands.

### Atomic Cookie Persistence (AUTH-05)

- **D-19:** **`_save_cookie()` keeps living in `web/routes.py`** but switches to **tmp + `os.replace()`**. Implementation:
  1. Read existing config text (or build default if file absent), apply the cookie substitution in memory as today
  2. Write resulting bytes to `_config.toml.tmp.{os.getpid()}.{threading.get_ident()}` in the same directory as `_config.toml`
  3. `os.replace(tmp, _config.toml)` — atomic on POSIX, atomic-on-same-volume on Windows; same-directory guarantees the volume invariant
  4. On any exception during steps 1–3, attempt `tmp.unlink(missing_ok=True)` cleanup so we don't leave stale tmp files
  Mirrors the pattern in `archive/store.py:_tmp_path()` exactly. PID/TID suffix avoids collision if two writers ever attempt simultaneously (last writer wins on the rename, but neither corrupts the target).
- **D-20:** Helper does NOT move into `config.py` or `auth.py` in this phase — minimum disruption. If a future phase adds more config-write sites, lift it out then.

### Claude's Discretion

- **Exact Wattpad probe endpoint URL** — `GET /api/v3/users/me` is the default candidate per STATE.md, but the researcher must verify the response shape (200 on valid, 401/403/redirect on invalid) against a manual probe before committing. If `/users/me` doesn't exist or returns 200 even for invalid cookies, fall back to: try `/api/v3/internal/auth/check` or hit `/api/v3/users/{me}/notifications` (notifications endpoints typically require auth even when story endpoints don't). Document the chosen endpoint in the planner's research notes.
- **Redirect detection mechanics** — httpx `follow_redirects=True` is set in `build_client()`. Either disable redirects for the validation probe specifically or check `resp.history` for `/login` Location headers. Planner picks whichever keeps the call site readable.
- **Transient client construction in `setup_post`** — to validate the submitted cookie before saving, code must build a `Config` instance with `cookie=submitted_value` (other fields copied from the live `cfg`) and a `RateLimitedClient` around it. Either do this inline in `setup_post` or expose a helper `validate_cookie_value(cfg: Config, candidate: str) -> None` in `auth.py`. Planner's choice.
- **Test fixture shapes** — synthetic httpx mock responses for 401/403/redirect-to-login (AUTH-01, AUTH-04); blank-cookie CLI invocation (success criterion #1); /setup POST with bad cookie (success criterion #2); mid-job 401 simulation by injecting a status-403 response into a `JobDeps`-stubbed `fetch_chapter_html()` and asserting `JobRunner` ends `failed` (success criterion #3); crash-during-write simulation via `monkeypatch` of `os.replace()` to raise mid-call (success criterion #4).
- **Error banner styling in setup.html** — Phase 2 isn't a UI phase; banner color/copy can match existing project style without external research.
- **Whether to log a `logger.warning` on every 401/403 inside `RateLimitedClient.get()`** before raising — probably yes (loud failures principle), but the log shape is planner's call. The test for AUTH-04 should not assert on the warning text.

### Folded Todos

None — `gsd-tools todo match-phase 2` returned zero matches.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level

- `.planning/REQUIREMENTS.md` §"Authentication" — AUTH-01 through AUTH-05; locks the names `AuthError` and `AuthFailedError`, the validation scope (archive commands), and the atomic-write requirement
- `.planning/PROJECT.md` §Constraints — single-process, Windows-first portability, Wattpad-ToS minimization (don't add user-agent distinctiveness), no new deps beyond stdlib + nh3
- `.planning/PROJECT.md` §Active — bullet "Validate Wattpad cookie on save (CLI + `/setup`)" frames the user-visible behavior
- `.planning/ROADMAP.md` §"Phase 2: Auth hardening" — Goal statement and four success criteria; verifier checks these literally
- `.planning/STATE.md` §Blockers/Concerns — flagged: "verify GET /api/v3/users/me returns non-200 for expired cookie during Phase 2 implementation"

### Phase 1 carry-forward

- `.planning/phases/01-local-hardening-fixes/01-CONTEXT.md` §Decisions §"Cap configurability" — establishes module-constant pattern (`_MAX_*` lowercase-leading-underscore); auth probe URL constant in `auth.py` should follow (e.g., `_PROBE_URL = "https://www.wattpad.com/api/v3/users/me"`)
- `.planning/phases/01-local-hardening-fixes/01-CONTEXT.md` §Code Context — `ResolveError` / `RenderError` precedent for new exception classes; `logger.exception` for caught exceptions, `logger.warning` for recoverable issues

### Codebase intel

- `.planning/codebase/CONCERNS.md` §"Cookie expiration not enforced" — origin of all five AUTH-* requirements; cites `wattpad_crawler/client.py:13-20` and `wattpad_crawler/web/routes.py:54-76` as fix sites
- `.planning/codebase/CONCERNS.md` §"Config file can be corrupted by concurrent writes" — origin of AUTH-05; recommends "atomic write pattern (write to temp, then rename)"
- `.planning/codebase/CONVENTIONS.md` §"Naming Patterns", §"Error Handling" — custom exceptions inherit directly from `Exception`; pipe-syntax unions; `_lowercase` for module-level private constants
- `.planning/codebase/STRUCTURE.md` §"Where to Add New Code" — top-level package modules house cross-cutting concerns; `wattpad_crawler/auth.py` is the right home for D-23
- `.planning/codebase/ARCHITECTURE.md` — layered architecture; `RateLimitedClient.get()` is the choke point all API calls flow through (justifies D-13 single-detection-site choice)
- `.planning/codebase/INTEGRATIONS.md`, `.planning/codebase/STACK.md` — httpx version + features in use; no async refactor needed for this phase

### Files to edit (verified during scout)

- `wattpad_crawler/auth.py` — **NEW** module: `validate_cookie()`, `AuthError`, `AuthFailedError`, probe URL constant
- `wattpad_crawler/client.py:56-89` — `RateLimitedClient.get()` — insert 401/403 fast-fail branch + `from wattpad_crawler.auth import AuthFailedError`
- `wattpad_crawler/cli.py:72-106` — `main()` — call `_require_auth()` (or equivalent) at the top of `story` / `url` / `library` / `list` branches; catch `AuthError` and exit 2 with formatted message
- `wattpad_crawler/jobs.py:114-152` — `archive_story()` per-part try/except — re-raise `AuthFailedError`; emit `auth.failed` event before re-raise
- `wattpad_crawler/web/routes.py:22-45` — `_save_cookie()` — switch to tmp + `os.replace()`; add cleanup on exception
- `wattpad_crawler/web/routes.py:69-76` — `setup_post()` — validate before save, three-category error handling, render setup.html with attempted_cookie_masked + error_kind/error_message on failure
- `wattpad_crawler/web/templates/setup.html` — render an error banner if `error_kind` set; show `attempted_cookie_masked` next to or in place of `current_cookie_masked` on errored re-render

### Test fixture sites

- `tests/unit/test_client.py` — assert `RateLimitedClient.get()` raises `AuthFailedError` on first 401/403 without retrying (AUTH-04)
- `tests/unit/test_cli.py` — assert blank-cookie `archive` invocation prints AuthError to stderr and exits 2 (success criterion #1)
- `tests/unit/test_web_routes.py` — assert `/setup` POST with bad cookie re-renders 400 with error banner and does not modify `_config.toml` (success criterion #2 / AUTH-03)
- `tests/unit/test_jobs.py` — assert `archive_story()` propagates `AuthFailedError` so JobRunner ends job `failed`, and emits `auth.failed` event (success criterion #3 / AUTH-04)
- New `tests/unit/test_auth.py` — assert `validate_cookie()` raises `AuthError` on 401/403/redirect, returns None on 200, propagates `httpx.RequestError` unchanged (AUTH-01)
- Either `test_web_routes.py` or new `test_save_cookie.py` — monkeypatch `os.replace` to raise mid-call, assert `_config.toml` is either unchanged or fully written (success criterion #4 / AUTH-05)

### External (researcher to fetch / verify)

- Wattpad API v3 auth-probe behavior — confirm `GET /api/v3/users/me` (or equivalent) returns 200 on valid cookie, 401/403/redirect-to-login on invalid; verify against a real expired cookie if possible. STATE.md flags this as required during Phase 2.
- httpx `follow_redirects` interaction with 3xx-to-/login — researcher confirms whether to disable redirects on the probe or inspect `resp.history`.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`RateLimitedClient.get(url, *, max_attempts=N)` already accepts max_attempts** (`client.py:56`) — D-04 just passes `max_attempts=1` for the probe, no client refactor needed.
- **`_mask(s: str)` helper in `web/routes.py:48-51`** — already used to render `current_cookie_masked`. Reuse for `attempted_cookie_masked` (D-11) verbatim.
- **`@dataclass(frozen=True)` Config in `config.py:10-16`** — D-12 needs to clone Config with a different cookie value: `dataclasses.replace(cfg, cookie=submitted_value)` is the idiomatic pattern.
- **`_tmp_path()` pattern in `archive/store.py`** — PID/TID suffix + `os.replace()` for atomic writes; copy the pattern verbatim for D-19.
- **`logger = logging.getLogger(__name__)`** in every module — `auth.py` follows the same pattern; warning-on-401 (Claude's Discretion) uses `logger.warning`.
- **`emit("kind", {"data": ...})` callback** in `archive_story()` — D-17 reuses the existing `emit` parameter, no new plumbing needed for the `auth.failed` event.
- **`JobRunner` already catches uncaught Exception → `job.set_failed`** (`web/runner.py`) — D-16 propagation flows through the existing path; no JobRunner changes needed.

### Established Patterns

- **Custom exceptions inherit from `Exception` directly** — `class AuthError(Exception)` and `class AuthFailedError(AuthError)` follow the precedent set by `ResolveError`, `RenderError`, `ConfigError`.
- **No try/except in `cli.py:main()` currently** — adding a top-level `except AuthError` block to format-and-exit-2 (D-08) is a small, contained addition. Watch the `finally: manifest.close(); client.close()` block — already correctly cleaning up, AuthError exit must run after cleanup.
- **Templates in `web/templates/` use Jinja2 auto-escape** — the error banner content (D-10) and `attempted_cookie_masked` are auto-escaped by default, no XSS risk from showing back submitted text.
- **JobRunner emits `__status__` sentinel after job completion** — `auth.failed` (D-17) emits BEFORE the failure propagates, so it lands before `__status__: failed` in the SSE stream — natural ordering.
- **`from wattpad_crawler.X import Y` (absolute imports throughout)** — all new imports follow.

### Integration Points

- **`RateLimitedClient.get()` at `client.py:56`** — single insertion point for D-13. The check goes between `resp = self._client.get(...)` (line 64) and the existing 429 check (line 73). Critical: the check must come BEFORE `raise_for_status()` (line 83) to avoid the existing path raising `HTTPStatusError` instead of `AuthFailedError`.
- **`cli.py:main()` lines 79–92 (subcommand dispatch)** — `_require_auth(cfg, client)` called at the top of each of the four branches. The existing `try/finally` for cleanup wraps this naturally.
- **`web/routes.py:setup_post`** — single function rewrite. Currently 8 lines (69–76); after Phase 2 it grows to ~25 lines but stays in the same file.
- **`jobs.py:archive_story()` per-part except block (lines 144–152)** — single insertion: `except AuthFailedError: emit("auth.failed", {...}); raise` BEFORE the broad `except Exception as e:`.
- **`web/templates/setup.html`** — minimal additions: `{% if error_kind %}` banner; switch the cookie input value attribute to use `attempted_cookie_masked or current_cookie_masked`. Existing template structure unchanged.
- **`tests/unit/test_client.py` and friends** — Phase 1 already established the unit-test pattern of monkeypatching deps; AUTH tests follow the same shape.

</code_context>

<specifics>
## Specific Ideas

- **Loud failure philosophy:** the entire phase exists because silent dead-cookie failures cost the user hours. Every decision favors loud-and-immediate over polite-and-deferred. `auth.failed` SSE event (D-17), 3-category /setup error banner (D-10), exit 2 with remediation hint (D-08) are all in service of this.
- **Re-use, don't re-invent:** atomic write copies `archive/store.py`'s pattern; probe transport reuses `RateLimitedClient`; error class hierarchy mirrors `ResolveError` precedent. New code surface is minimal — `auth.py` (~80 lines), one branch in `client.py.get()`, one helper call per CLI branch, one block in `setup_post`, one re-raise + event in `archive_story()`, one helper rewrite for `_save_cookie()`.
- **Detection at the choke point:** D-13 puts 401/403 detection in `RateLimitedClient.get()` so it covers every API call site uniformly — `fetch_story()`, `fetch_inline_comments()`, `fetch_end_comments()`, `fetch_library()`, `fetch_list_story_ids()`, and the validation probe itself. One branch protects all five surfaces.
- **STATE.md probe URL flag:** STATE.md `Blockers/Concerns` already records that `GET /api/v3/users/me`'s behavior with an expired cookie needs manual verification during Phase 2. Researcher MUST address this before the planner locks the URL — it's a knowable unknown.

</specifics>

<deferred>
## Deferred Ideas

- **Cookie refresh / re-login automation** — out of scope for v1 (REQUIREMENTS.md §Future Auth, AUTH-V2-01 deferred); manual paste-from-browser is the v1 UX.
- **OS keyring / credentials manager integration** — overkill for single-user local tool; explicit in PROJECT.md §Constraints.
- **Persisting auth status to manifest** — REQUIREMENTS.md AUTH-V2-01 already deferred; cosmetic until then.
- **Cover-fetch auth handling** — covers tolerate failures already (jobs.py:97-98 catches Exception around cover fetch); no AuthFailedError special case there. If a 401 ever comes back from a cover URL, current behavior (warn + skip) is acceptable.
- **`--skip-auth-check` opt-out flag** — explicitly rejected in D-07; revisit only if a real-world need surfaces.
- **Lifting `_save_cookie()` out of `web/routes.py` into `config.py`** — explicitly deferred in D-20; revisit if v2 adds more config-write sites.
- **Validation retries on transient network errors** — explicitly rejected; network errors propagate as their own type (D-02), user retries `/setup` manually if it was a flap.
- **Distinct error pages for `/setup` failures (not banner-on-form)** — out of scope; banner re-render is the chosen UX (D-09).

</deferred>

---

*Phase: 02-auth-hardening*
*Context gathered: 2026-05-03*
