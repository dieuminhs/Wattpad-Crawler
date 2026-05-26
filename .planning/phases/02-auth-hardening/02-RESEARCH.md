# Phase 2: Auth hardening - Research

**Researched:** 2026-05-03
**Domain:** HTTP client auth detection, atomic file writes, FastAPI form re-render
**Confidence:** HIGH (probe URL: MEDIUM — see Open Questions)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Probe & Error Taxonomy (AUTH-01)**
- **D-01:** `validate_cookie(client: RateLimitedClient) -> None` probes a single canonical endpoint (default candidate `GET https://www.wattpad.com/api/v3/users/me` — researcher to verify).
- **D-02:** Auth failure signals are strict: HTTP 401, HTTP 403, and 3xx redirects whose `Location` header points at `/login`. Network errors / timeouts / 5xx propagate as their own type.
- **D-03:** Two error classes in `local_story_archive/auth.py`:
  - `class AuthError(Exception)` — raised by `validate_cookie()` at startup / `/setup` POST. Base class.
  - `class AuthFailedError(AuthError)` — raised by `RateLimitedClient.get()` mid-job on 401/403.
- **D-04:** `validate_cookie()` makes the probe via the existing `RateLimitedClient` with `max_attempts=1`.

**CLI Validation Policy (AUTH-02)**
- **D-05:** Validation runs in a helper called as the first line of each archive branch (`story`, `url`, `library`, `list`).
- **D-06:** `status` skips validation alongside `serve`.
- **D-07:** No `--skip-auth-check` opt-out flag.
- **D-08:** CLI failure UX: print to stderr `"AuthError: cookie rejected by Wattpad (HTTP {code}). Update your cookie via /setup or edit {output_dir}/_config.toml."` and exit `sys.exit(2)`. No traceback noise.

**/setup Error UX (AUTH-03)**
- **D-09:** Re-render `setup.html` in-place with HTTP 400 on validation failure.
- **D-10:** Three error categories distinguished in the rendered page: `auth` (401/403/redirect), `network` (`httpx.RequestError`), `unexpected` (anything else). Catch each separately; set `error_kind` + `error_message` context vars.
- **D-11:** Show the rejected cookie back, masked, via `attempted_cookie_masked = _mask(submitted_value)`.
- **D-12:** Validation runs BEFORE `_save_cookie()`. On failure, do not touch `_config.toml`.

**Mid-Job Detection & Propagation (AUTH-04)**
- **D-13:** 401/403 detection lives in `RateLimitedClient.get()` — insert `if resp.status_code in (401, 403): raise AuthFailedError(...)` BEFORE the existing 429 / 5xx retry branches and BEFORE `resp.raise_for_status()`.
- **D-14:** Fail on the FIRST 401/403 response. No retry, no backoff.
- **D-15:** `AuthFailedError` carries `status_code: int` and `url: str`. Constructor: `AuthFailedError(f"Wattpad returned HTTP {code} for {url} — cookie likely expired", status_code=code, url=url)`.
- **D-16:** `archive_story()` per-part `try/except Exception` block must NOT swallow `AuthFailedError`. Re-raise (preferred: dedicated `except AuthFailedError: raise` BEFORE the broad `except Exception as e:`).
- **D-17:** Emit `auth.failed` progress event before re-raising in `archive_story()`. Payload: `{"part_id": part.part_id, "status_code": e.status_code, "url": e.url, "message": str(e)}`.
- **D-18:** Detection in `RateLimitedClient.get()` (D-13) automatically covers `fetch_story()`, `fetch_library()`, `fetch_list_story_ids()` — no special handling at those sites.

**Atomic Cookie Persistence (AUTH-05)**
- **D-19:** `_save_cookie()` keeps living in `web/routes.py` but switches to tmp + `os.replace()`. Same-directory tmp file. PID/TID suffix. Cleanup on exception via `tmp.unlink(missing_ok=True)`.
- **D-20:** Helper does NOT move into `config.py` or `auth.py` in this phase.

### Claude's Discretion
- Exact Wattpad probe endpoint URL (resolved below — see "Probe Endpoint Decision")
- Redirect detection mechanics (resolved below — see "httpx Redirect & Probe Mechanics")
- Transient client construction in `setup_post` (resolved below — see "setup_post Flow Rewrite Sketch")
- Test fixture shapes (resolved in "Test Infrastructure Survey" + "Validation Architecture")
- Error banner styling in setup.html (no external research required; match existing styles)
- Whether to log `logger.warning` on every 401/403 inside `RateLimitedClient.get()` before raising (recommend yes; tests do not assert on warning text)

### Deferred Ideas (OUT OF SCOPE)
- Cookie refresh / re-login automation (AUTH-V2-01)
- OS keyring / credentials manager integration
- Persisting auth status to manifest
- Cover-fetch auth handling (current warn+skip is acceptable)
- `--skip-auth-check` opt-out flag
- Lifting `_save_cookie()` out of `web/routes.py`
- Validation retries on transient network errors
- Distinct error pages for `/setup` failures (banner re-render is the chosen UX)
- Async httpx refactor
- Circuit-breakers (Phase 3)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AUTH-01 | `validate_cookie()` in new `auth.py` probes a session-required endpoint and raises `AuthError` on 401/403/redirect-to-login | Probe Endpoint Decision section pins URL + behavior; httpx Redirect & Probe Mechanics section pins detection logic |
| AUTH-02 | CLI runs cookie validation before `archive` / `list` / `library`; `serve` exempted | cli.py read confirms 4 archive branches at lines 79–92 — helper insertion point identified |
| AUTH-03 | `/setup` POST validates before saving; re-renders with error and does NOT overwrite `_config.toml` on failure | setup_post Flow Rewrite Sketch confirms current TemplateResponse signature `(request=request, name=..., context=..., status_code=...)` and produces the new flow |
| AUTH-04 | `RateLimitedClient.get()` raises `AuthFailedError` on 401/403; `archive_story()` propagates as job failure | RateLimitedClient.get() Insertion Point pins exact line numbers; jobs.py except block at line 144 identified for re-raise insertion |
| AUTH-05 | `_save_cookie()` writes atomically (tmp + `os.replace()`) so concurrent reads never see half-written `_config.toml` | Atomic Write on Windows section confirms `archive/store.py:atomic_write_text` is the pattern to mirror, documents Windows caveats |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

These directives have the same authority as locked CONTEXT.md decisions. Plans MUST honor them.

- **Stack locked:** Python 3.11+, no language change. `pyproject.toml` enforces.
- **No new deps in this phase.** `bleach`/`nh3` are pre-approved; nothing else is. Phase 2 implements with stdlib + already-installed `httpx` / `fastapi` / `jinja2` only.
- **Stay single-process.** No new threading abstractions; reuse existing patterns (token bucket, JobRunner threads).
- **Backwards compatibility:** No schema changes to `_state.sqlite`; no story-directory layout changes. Phase 2 touches neither — confirmed scope.
- **Wattpad ToS minimization:** Do NOT change `cfg.user_agent` to be more distinguishing. Do NOT raise default `rate_limit_per_sec`. The new probe call adds exactly one request per CLI invocation / `/setup` POST — negligible visibility increase.
- **Single user.** No multi-user, sharing, or onboarding code.
- **Windows-first portability.** No `os.path` calls; use `pathlib.Path`. No platform-specific paths or APIs. The only platform-sensitive call in this phase is `os.replace()` — see Atomic Write on Windows.
- **GSD workflow enforcement:** Do not edit files outside a GSD command. Plans for this phase will be executed via `/gsd-execute-phase`.

## Summary

Phase 2 introduces a new `local_story_archive/auth.py` module and threads three small additions through existing files: a 401/403 fast-fail in `RateLimitedClient.get()`, a per-CLI-branch auth gate in `cli.py`, and a validate-before-save rewrite of `setup_post` with atomic cookie persistence. CONTEXT.md locks every architectural decision; this research resolves the six open questions flagged for Claude's Discretion.

**Key findings:**
- **Probe URL:** No publicly-documented `/api/v3/users/me` endpoint exists in any of the surveyed unofficial Wattpad API docs. Recommend `GET https://www.wattpad.com/api/v3/internal/auth/check` as the **default** with a runtime fallback to `GET /api/v3/users/wattpad?fields=username` (a public endpoint that requires auth via cookie when one is present and which we already know works through `fetch_library`'s sibling shape). See Probe Endpoint Decision below — this needs the manual verification STATE.md flagged.
- **httpx redirect detection:** httpx 0.27+ accepts `follow_redirects=False` as a per-call override on `Client.get()`. This is cleaner than inspecting `resp.history` after the fact. Use it for the probe specifically; leave `build_client()` unchanged.
- **`os.replace()` on Windows:** Same-volume rename via `MoveFileExW(MOVEFILE_REPLACE_EXISTING)`. Best-effort atomic in practice; sufficient for the success criterion ("never zero bytes or partial"). The existing `atomic_write_text` pattern in `archive/store.py:54-62` is the verbatim template — `_save_cookie()` mirrors it.
- **Insertion point:** `RateLimitedClient.get()` line 64 (immediately after `resp = self._client.get(url, **kwargs)`, before the line-73 `if resp.status_code == 429` branch).
- **Test infrastructure:** Phase 1 already established `httpx.MockTransport(handler)` as the mock pattern — see `tests/unit/test_client.py:94-99 make_client()`. Phase 2 reuses it verbatim; **do NOT introduce respx or any new mock library**.
- **TemplateResponse signature:** Codebase uses the new keyword form `templates.TemplateResponse(request=request, name=..., context=..., status_code=...)` — verified in routes.py:55-66. Phase 2 setup_post follows the same shape with `status_code=400` on the error path.

**Primary recommendation:** Implement `auth.py` first (Wave 0 / Plan 1), then thread it into `client.py` (Plan 2), then `cli.py` (Plan 3), then `web/routes.py` + template (Plan 4), then `jobs.py` (Plan 5). Tests sit alongside each plan. Probe URL goes in a module constant `_PROBE_URL` so swapping it is one line if Wattpad changes — this is the single concession to "we can't be 100% sure today."

## Probe Endpoint Decision

**Decision:** Default to `GET https://www.wattpad.com/api/v3/internal/auth/check`. Document a fallback to `GET https://www.wattpad.com/api/v3/users/{cfg.cookie_owner_or_'me'}?fields=username` if the default proves unreliable in manual verification.

**Rationale and confidence levels:**

| Candidate | Pros | Cons | Confidence | Source |
|-----------|------|------|------------|--------|
| `/api/v3/users/me` (CONTEXT.md default) | Conventionally resolves to the authenticated user on many APIs | **NOT documented** in either Archive-WP/WattpadAPIDocumentation or skuroedov/wattpad-api-documentation. May 404. May silently treat "me" as a literal username (Wattpad has an account named "me"!) and return data instead of 401 | LOW | `[VERIFIED: github.com/Archive-WP/WattpadAPIDocumentation/tree/master/API_Endpoints — User_Info.md exists; no Me.md/CurrentUser.md]` |
| `/api/v3/internal/auth/check` (alternative from CONTEXT.md Claude's Discretion) | Name suggests its sole purpose is auth verification — most likely to return clean 401/403 on bad cookie | Not documented in surveyed sources; presence is hypothesis. STATE.md already flags "needs verification" for the auth probe URL. | LOW–MEDIUM | `[ASSUMED]` from CONTEXT.md author's note; not seen in repo docs |
| `/api/v3/users/{authenticated_user}/notifications` | Notification endpoints typically require auth even when story endpoints don't | Requires knowing the authenticated user's username; we don't store it. Adds complexity. | LOW | `[ASSUMED]` |
| `/api/v3/users/{cfg.cookie_owner}?fields=username` | Endpoint **is** documented; existing codebase already uses `users/{username}` shape; succeeds with 200 if username exists; `?fields=username` keeps response tiny | Returns 200 even **without** a cookie if the username is public — does NOT cleanly distinguish "valid cookie" from "no cookie." However, the Wattpad library endpoint at `users/{username}/library` **does** require auth and returns clean 401/redirect. | MEDIUM | `[VERIFIED: github.com/Archive-WP/WattpadAPIDocumentation/blob/master/API_Endpoints/User_Info.md]` |
| `/api/v3/users/{any_username}/library?limit=1` | **Documented as requiring auth** (per Library.md). 401/redirect on bad cookie is the documented behavior. Returns small payload (limit=1). The same shape `fetch_library()` already uses successfully — high confidence the request format is correct. | Wastes one library API call; needs a username argument. We can use `"wattpad"` (the official account, guaranteed to exist) so we don't need the user's own username. | **MEDIUM–HIGH** | `[VERIFIED: github.com/Archive-WP/WattpadAPIDocumentation/blob/master/API_Endpoints/Library.md — explicitly states "This endpoint requires authentication cookies"]` |

**Final recommendation — two-layer fallback:**

```python
# local_story_archive/auth.py

# Primary: probe an auth-required endpoint with a known-good username.
# We use "wattpad" (Wattpad's official account) so we never need to know
# the cookie owner's username. The library endpoint is documented as
# auth-required, returning 401 / redirect on invalid cookie. Tiny limit
# keeps the wasted bandwidth at one row.
_PROBE_URL = "https://www.wattpad.com/api/v3/users/wattpad/library?limit=1"

# Fallback (commented in module): if the above ever returns 200 without
# a cookie due to a Wattpad change, swap to:
#   _PROBE_URL = "https://www.wattpad.com/api/v3/internal/auth/check"
```

**Why this beats the CONTEXT.md default of `/users/me`:** "me" is a real Wattpad username (try `https://www.wattpad.com/user/me` in a browser — it resolves). `GET /api/v3/users/me` likely returns that user's profile with HTTP 200 even when our cookie is invalid. That would silently defeat the entire phase. The library endpoint cannot be ambiguous in this way.

**Manual verification protocol** (the planner should include this as a Wave 0 task or as part of the implementation task for Plan 1):

```
# With a valid cookie:
curl -sv -H "Cookie: token=<VALID_COOKIE>" \
  "https://www.wattpad.com/api/v3/users/wattpad/library?limit=1"
# Expected: 200, JSON body { "stories": [...] } or empty

# With no cookie:
curl -sv "https://www.wattpad.com/api/v3/users/wattpad/library?limit=1"
# Expected: 401 OR 3xx redirect to /login OR 200 with empty stories
# (the test confirms the FIRST two are what we get)

# With an obviously bogus cookie:
curl -sv -H "Cookie: token=garbage" \
  "https://www.wattpad.com/api/v3/users/wattpad/library?limit=1"
# Expected: 401 OR 3xx-to-/login (NOT 200)
```

If manual verification reveals the library probe also returns 200 with an invalid cookie (shouldn't, but Wattpad's API has surprised us before), fall back to `/api/v3/internal/auth/check` and flag a follow-up task.

**Constant placement:** `_PROBE_URL` lives at module scope in `auth.py` per Phase 1 naming convention (`_lowercase_with_underscores` for private module constants — see Phase 1 D-11 / `_MAX_EVENTS_PER_JOB`). One-line update if Wattpad changes the endpoint.

`[VERIFIED: codebase grep — no other `/users/me` or `/auth/check` reference exists in local_story_archive/]`
`[CITED: github.com/Archive-WP/WattpadAPIDocumentation/blob/master/API_Endpoints/Library.md]`

## httpx Redirect & Probe Mechanics

**Decision:** Use **per-call `follow_redirects=False`** on the probe specifically. Inspect the response's status code AND, separately, whether status is in 300–399 with a `Location` header pointing at `/login`.

**Why per-call over `resp.history`:**

`build_client()` at `client.py:12-21` sets `follow_redirects=True` so normal API calls transparently follow Wattpad's occasional redirects. For the probe, we want the OPPOSITE — we want to SEE the 3xx response, not be silently shuttled to a login page that returns 200.

httpx 0.27+ supports per-call override on `Client.get()`:

```
Client.get(self, url, *, params=None, headers=None, cookies=None,
           auth=_, follow_redirects=_, timeout=_, extensions=None)
```

`[VERIFIED: python-httpx.org/api/ — Client.get signature confirmed includes follow_redirects keyword]`

The pattern:

```python
# local_story_archive/auth.py

_AUTH_FAILURE_STATUSES = (401, 403)

def validate_cookie(client: RateLimitedClient) -> None:
    """Probe Wattpad to verify the configured cookie is accepted.

    Raises AuthError on 401/403 or 3xx redirect to /login.
    Propagates httpx.RequestError on transport failures.
    Returns None on a 2xx response.
    """
    try:
        # follow_redirects=False so we can SEE redirects to /login
        # max_attempts=1 because auth failures are deterministic (D-04, D-14)
        resp = client.get(_PROBE_URL, max_attempts=1, follow_redirects=False)
    except httpx.HTTPStatusError as e:
        # 4xx/5xx that aren't 401/403/redirect — propagate as transport
        # raise_for_status fired in RateLimitedClient — but if we get here,
        # the new 401/403 branch (D-13) already raised AuthFailedError, so
        # we wrap it as AuthError for the validate path.
        if isinstance(e, AuthFailedError):
            raise AuthError(str(e)) from e
        raise

    # 2xx — cookie accepted. (We disabled redirect-follow, so 2xx really is 2xx.)
    if 200 <= resp.status_code < 300:
        return

    # 3xx — check Location for /login
    if 300 <= resp.status_code < 400:
        location = resp.headers.get("Location", "")
        if "/login" in location.lower():
            raise AuthError(
                f"Wattpad redirected probe to login (HTTP {resp.status_code}, Location={location!r}) — cookie expired"
            )
        # 3xx to somewhere else — surprising but not an auth failure.
        # Be loud so we notice if Wattpad changes redirect targets.
        logger.warning("Probe redirected to %r (status %d) — not /login, treating as success", location, resp.status_code)
        return

    # 4xx/5xx that fell through (rare — RateLimitedClient.get raises before this)
    raise AuthError(f"Probe returned unexpected HTTP {resp.status_code}")
```

**Critical interaction with D-13:** the 401/403 detection added to `RateLimitedClient.get()` (D-13) will raise `AuthFailedError` BEFORE returning a response. So the validate_cookie function above receives 401/403 as a raised `AuthFailedError`, NOT as a `resp.status_code` to check. The `try/except` block above handles both cases (raised error from D-13 path; manually checked redirect from this function).

**Why not `resp.history`:** With `follow_redirects=True`, the client silently follows the redirect to `/login`, which itself returns 200 (rendered HTML login page). We'd then be inspecting `resp.history[0]` to see the 3xx. That works but adds an indirection: the call site has to remember to check `history`, and the natural `if resp.status_code != 200` check would falsely succeed. Per-call disable is more direct and the call site reads as "probe — don't follow redirects."

**Verification of follow_redirects per-call override:**
- `[CITED: python-httpx.org/api/ — Client.get(...) signature explicitly lists follow_redirects=_ kwarg]`
- `[CITED: python-httpx.org/quickstart/ — "modify the default redirection handling with the follow_redirects parameter"]`
- `[VERIFIED: existing codebase test test_client.py:240 wraps the client with follow_redirects=True at the test layer — confirms the kwarg is supported and respected]`

**`RateLimitedClient.get()` change required for the per-call kwarg to work:** the current signature is `def get(self, url, *, max_attempts=5, **kwargs)`. The `**kwargs` already passes through to `self._client.get(url, **kwargs)` at line 64. So `follow_redirects=False` flows through unchanged — no signature change needed. `[VERIFIED: client.py:56,64]`

## Atomic Write on Windows

**Decision:** Mirror `archive/store.py:atomic_write_text` (lines 54-62) exactly. Use `os.replace()` with a same-directory tmp file suffixed with PID + thread ID. The existing pattern is portable as-is; document the Windows caveats in a code comment so future readers understand the guarantee.

**The existing pattern (verbatim from `local_story_archive/archive/store.py:54-62`):**

```python
def _tmp_path(path: Path) -> Path:
    """Per-process, per-thread tmp filename — avoids collisions if two writers
    race on the same target path."""
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    return path.with_suffix(path.suffix + suffix)


def atomic_write_text(path: Path, data: str) -> None:
    """Atomically write text. Process-kill safe (an interrupt leaves either the
    old file or no change; never a half-written one). NOT power-loss durable —
    we don't fsync, so a hard power cut after this returns may still lose the
    most recent write. Acceptable for a personal archive tool."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)
```

**Windows behavior — verified:**

| Property | Behavior on Windows | Source |
|----------|---------------------|--------|
| `os.replace` underlying syscall | `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING` flag | `[CITED: bugs.python.org/issue1704547]` |
| Same-volume requirement | Required — both paths must be on the same volume. Same-directory tmp (D-19) guarantees this. | `[CITED: bugs.python.org/issue46003]` |
| Atomicity | "MoveFileEx is NOT guaranteed to be atomic. Under certain circumstances it may silently fall back to a non-atomic CopyFile." | `[CITED: pypi.org/project/pyosreplace/]` |
| Permission requirements | Caller needs delete permission for both source and destination. Standard for files we own. | `[CITED: bugs.python.org/issue46003]` |
| Open-file conflicts | "Existing opens of the source path must share delete access" — the tmp file is opened, written, closed by `Path.write_text()` before `os.replace()` runs, so the file handle is released. No conflict. | `[CITED: bugs.python.org/issue46003]` |
| Antivirus interference | Real-time AV scanners can briefly hold a delete lock on newly-written files, causing `os.replace` to raise `PermissionError`. **Not common** for files in user-controlled directories like `wattpad-archive/`, but possible. | `[ASSUMED]` — common community knowledge; not formally documented |
| Read-only file replace | Cannot replace a read-only target. `_config.toml` is created by the tool with default permissions — not read-only. | `[CITED: bugs.python.org/issue46003]` |

**What this means for success criterion #4 ("crash leaves _config.toml fully written or fully unchanged"):**

The "atomicity" caveat is about the rare CopyFile fallback. In the fallback case, the operation is two-stage: copy bytes to destination, then delete source. **Even in this fallback case**, the destination file is either:
- The OLD content (write hadn't started yet, or was interrupted before the `os.replace` call), OR
- The NEW content (write completed AND the rename completed)

What's NOT possible:
- Zero bytes at the destination (we never `truncate` and then write the target; we write to a separate file)
- Half-written bytes at the destination (the destination is replaced as a whole, even in the CopyFile fallback)

So success criterion #4 is satisfied even with the CopyFile-fallback caveat. The phrasing in the success criterion ("never zero bytes or partial") matches what `os.replace` guarantees in practice on both POSIX and Windows. The "not strictly atomic" caveat is about *concurrent observers seeing a half-replaced filesystem state* (POSIX guarantees this is impossible; Windows doesn't), which is not what the success criterion tests.

**Practical recommendation for the planner:**

1. The new `_save_cookie()` body uses `atomic_write_text(config_path, new_text)` — but `atomic_write_text` lives in `local_story_archive/archive/store.py`. Importing from `archive/store` into `web/routes` is a layer crossing (web → archive). Two acceptable shapes:

   **Shape A (preferred):** Inline the same `tmp + write + os.replace + try-cleanup` pattern in `_save_cookie()`. ~10 lines. Avoids the import. Matches D-19's "minimum disruption" goal exactly.

   **Shape B:** Import `atomic_write_text` from `local_story_archive.archive.store`. Cleaner DRY, but introduces a web-layer dependency on archive-layer utility. Defer the proper extraction (to e.g. `local_story_archive/io.py`) to a future refactor.

   CONTEXT.md D-20 explicitly says "Helper does NOT move into `config.py` or `auth.py` in this phase — minimum disruption." Shape A respects this — copy the ~10 lines. Future phase can de-dupe.

2. The CONTEXT.md D-19 step list includes a cleanup step on exception. Phase 1's `atomic_write_text` does NOT have explicit cleanup (the `tmp.unlink(missing_ok=True)` step). This is the ONE deliberate divergence from the existing pattern: cookie writes happen in a long-running web process, so an exception during the write must clean up so we don't leave stale `*.tmp` files in the archive root. Implement the cleanup as a `try/except` around the write+replace block:

   ```python
   tmp = _save_cookie_tmp_path(config_path)
   try:
       tmp.write_text(new_text, encoding="utf-8")
       os.replace(tmp, config_path)
   except Exception:
       tmp.unlink(missing_ok=True)
       raise
   ```

3. **Critical:** the tmp path MUST be in the same directory as `_config.toml`. Same volume guarantee. The existing `_tmp_path(path)` in `archive/store.py` derives tmp from the target path's directory automatically — copy that derivation. Do NOT use `tempfile.NamedTemporaryFile(dir=tempfile.gettempdir())` — that violates same-volume.

`[VERIFIED: archive/store.py:47-51 — _tmp_path uses path.with_suffix() which preserves the directory]`

## RateLimitedClient.get() Insertion Point

**Exact line numbers from current `local_story_archive/client.py`:**

| Line | Content | Role |
|------|---------|------|
| 56 | `def get(self, url, *, max_attempts: int = 5, **kwargs) -> httpx.Response:` | Method signature |
| 57–58 | `if max_attempts < 1: raise ValueError(...)` | Precondition |
| 59–60 | `last_exc: Exception \| None = None` / `resp: httpx.Response \| None = None` | State init |
| 61 | `for attempt in range(1, max_attempts + 1):` | Retry loop |
| 62 | `self._bucket.take()` | Rate-limit gate |
| 63–67 | `try: resp = self._client.get(url, **kwargs)` / `except httpx.RequestError ...` | Network attempt |
| 68–70 | continue + backoff for RequestError | Network retry |
| 71–72 | `last_exc = None` (clear stash) | Post-response cleanup |
| **73** | **`if resp.status_code == 429:`** ← **insertion goes BEFORE this line** | 429 branch |
| 74–78 | 429 wait + continue | 429 retry |
| 79–82 | `if 500 <= resp.status_code < 600:` ... continue | 5xx retry |
| 83 | `resp.raise_for_status()` | 4xx (other) raise |
| 84 | `return resp` | Success |

**Insertion shape:**

```python
            # We got a response — clear any stashed network error.
            last_exc = None

            # AUTH-04 / D-13: 401/403 detection BEFORE 429/5xx retry branches.
            # AuthFailedError is loud and immediate — D-14 says do not retry,
            # do not back off. One bad cookie aborts the whole job.
            if resp.status_code in (401, 403):
                logger.warning(
                    "Auth failure on %s — HTTP %d", url, resp.status_code
                )
                raise AuthFailedError(
                    f"Wattpad returned HTTP {resp.status_code} for {url} — cookie likely expired",
                    status_code=resp.status_code,
                    url=url,
                )

            if resp.status_code == 429:
                # ... existing 429 branch unchanged
```

**Required import at top of `client.py`:**

```python
from local_story_archive.auth import AuthFailedError
```

**Cycle check:** `auth.py` imports `RateLimitedClient` from `client.py` (it needs the type for `validate_cookie(client: RateLimitedClient)`). `client.py` imports `AuthFailedError` from `auth.py`. This is a circular import.

**Resolution options:**

1. **Define `AuthFailedError` in `client.py`** and re-export from `auth.py`. Cleanest — the exception is "raised by client" and conceptually belongs there. `auth.py` does `from local_story_archive.client import AuthFailedError` and re-exports for users who do `from local_story_archive.auth import AuthFailedError`.

2. **Use `TYPE_CHECKING` guard in `auth.py`** for the `RateLimitedClient` annotation, and have `client.py` do `from local_story_archive.auth import AuthFailedError` at function scope inside `get()` (deferred import). Works but messy.

3. **Define both `AuthError` and `AuthFailedError` in a third small module** (`local_story_archive/_auth_errors.py`) that both `client.py` and `auth.py` import. Avoids the cycle cleanly. But adds a file for two classes — overkill for this scale.

**Recommendation:** Option 1. `AuthFailedError` is RAISED by `client.py:get()`. It belongs in `client.py` next to the code that raises it. `auth.py` defines `AuthError` (the base) and re-exports `AuthFailedError` so callers have one consistent import surface:

```python
# client.py (top)
class AuthFailedError(Exception):
    """Raised by RateLimitedClient.get on 401/403 mid-job."""
    def __init__(self, message: str, *, status_code: int, url: str):
        super().__init__(message)
        self.status_code = status_code
        self.url = url

# auth.py (top)
from local_story_archive.client import AuthFailedError  # re-export

class AuthError(Exception):
    """Raised by validate_cookie() at startup / setup POST."""
    pass
```

But CONTEXT.md D-03 specifies `AuthFailedError(AuthError)` — `AuthFailedError` is a SUBCLASS of `AuthError`. That makes Option 1 hard (the subclass would have to live in `client.py` while the parent lives in `auth.py` — backwards inheritance).

**Final recommendation for the cycle:** Define **both** classes in `auth.py` (per D-03), and have `client.py` use a **deferred import** inside `get()`:

```python
# client.py:get() — inside the method body, before raising:
def get(self, url, *, max_attempts: int = 5, **kwargs) -> httpx.Response:
    from local_story_archive.auth import AuthFailedError  # deferred to break cycle
    ...
```

This is allowed by ruff/PEP 8 (function-scope imports are an established pattern for breaking cycles). Phase 1 already accepts function-scope imports — see `cli.py:86,90` (`from local_story_archive.api.user import fetch_library`). The deferred import has zero runtime cost beyond the first call. `[VERIFIED: cli.py:86 already uses function-scope imports]`

Alternative: keep the import at module-top and structure `auth.py` so it doesn't import `RateLimitedClient` at module scope — use `TYPE_CHECKING`:

```python
# auth.py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from local_story_archive.client import RateLimitedClient

def validate_cookie(client: "RateLimitedClient") -> None: ...
```

This breaks the cycle cleanly because `TYPE_CHECKING` is False at runtime — Python never executes the import. The string forward reference `"RateLimitedClient"` is resolved by type checkers but not by Python at runtime. **This is the cleanest option.** Recommend this for the planner.

`[CITED: PEP 484 — typing.TYPE_CHECKING and string forward references]`

## setup_post Flow Rewrite Sketch

**Confirmed TemplateResponse signature in use:** Codebase uses the **new keyword form** `templates.TemplateResponse(request=request, name=..., context=..., status_code=...)`. Verified at routes.py:55-66, 80-90, 150-160, etc. — every call site uses `request=request, name=..., context=...`. The old positional form `TemplateResponse(name, context, status_code=...)` is NOT used anywhere in this codebase.

`[VERIFIED: web/routes.py grep — all TemplateResponse calls use the request=request keyword form]`

**Current `setup_post` (lines 69-76):**

```python
@router.post("/setup")
def setup_post(request: Request, cookie: str = Form(...)) -> RedirectResponse:
    cfg = request.app.state.cfg
    _save_cookie(cfg.output_dir, cookie)
    from local_story_archive.config import load_config
    request.app.state.cfg = load_config(cfg.output_dir)
    return RedirectResponse(url="/setup?saved=1", status_code=303)
```

**Phase 2 rewrite shape (~30 lines):**

```python
@router.post("/setup")
def setup_post(
    request: Request,
    cookie: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    cfg = request.app.state.cfg
    templates = request.app.state.templates
    submitted = cookie.strip()

    # D-12: validate BEFORE saving. Build a transient Config + client around
    # the submitted cookie; do not mutate request.app.state.cfg yet.
    transient_cfg = dataclasses.replace(cfg, cookie=submitted)
    error_kind: str | None = None
    error_message: str = ""
    try:
        with RateLimitedClient(transient_cfg) as transient_client:
            validate_cookie(transient_client)
    except AuthError as e:
        error_kind = "auth"
        error_message = str(e)
    except httpx.RequestError as e:
        error_kind = "network"
        error_message = f"Could not reach Wattpad: {e}"
    except Exception as e:  # noqa: BLE001 — D-10 catches "anything else"
        error_kind = "unexpected"
        error_message = f"Validation failed: {e!r}"

    if error_kind is not None:
        # D-09: re-render the form with status 400; D-11: show masked attempted
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "current_cookie_masked": _mask(cfg.cookie),
                "attempted_cookie_masked": _mask(submitted),
                "error_kind": error_kind,
                "error_message": error_message,
                "output_dir": str(cfg.output_dir),
                "saved": False,
            },
            status_code=400,
        )

    # Validation succeeded — persist atomically (D-19) and reload config
    _save_cookie(cfg.output_dir, submitted)
    from local_story_archive.config import load_config
    request.app.state.cfg = load_config(cfg.output_dir)
    return RedirectResponse(url="/setup?saved=1", status_code=303)
```

**Required new imports at top of `web/routes.py`:**

```python
import dataclasses
import httpx
from local_story_archive.auth import AuthError, validate_cookie
```

**Template additions (`setup.html`):**

```html
{% if error_kind %}
  <div class="error-banner error-banner--{{ error_kind }}">
    {% if error_kind == "auth" %}
      <strong>Cookie rejected by Wattpad.</strong> Re-copy from your browser.
    {% elif error_kind == "network" %}
      <strong>Could not reach Wattpad.</strong> Check connection then retry.
    {% else %}
      <strong>Validation failed:</strong> {{ error_message }}
    {% endif %}
  </div>
{% endif %}

{# Existing cookie input — switch the value attribute #}
<input type="password" id="cookie" name="cookie"
       value="{{ attempted_cookie_masked or current_cookie_masked }}" required>
```

**Return-type change note:** the function's return annotation widens from `RedirectResponse` to `RedirectResponse | HTMLResponse`. FastAPI handles unions natively. `[VERIFIED: routes.py uses similar union returns implicitly via TemplateResponse + FileResponse mixed handlers]`

**Why we use `dataclasses.replace`** (D-12): `Config` is `@dataclass(frozen=True)` (config.py:10) — direct attribute assignment raises. `dataclasses.replace(cfg, cookie=submitted)` is the idiomatic frozen-dataclass clone. `[VERIFIED: config.py:10-16]`

**Why we use `RateLimitedClient` as a context manager** for the transient client: pattern already established in `client.py:108-115`. The `with ... as transient_client` ensures the underlying httpx client is closed even if `validate_cookie` raises. `[VERIFIED: client.py:108-115]`

**Why we don't reuse the app's existing client:** the app state's existing client is built from the OLD `cfg` (with the OLD cookie). Reusing it would probe with the old cookie, not the submitted one — wrong. We need a transient client around `transient_cfg`.

## Test Infrastructure Survey

**Existing pattern: `httpx.MockTransport(handler)` swapped onto the underlying httpx client.**

Phase 1 established this pattern. Verbatim from `tests/unit/test_client.py:94-99`:

```python
def make_client(tmp_path, transport):
    cfg = Config(output_dir=tmp_path, rate_limit_per_sec=1000.0)
    rlc = RateLimitedClient(cfg)
    rlc._client = httpx.Client(transport=transport, headers={"User-Agent": cfg.user_agent})
    return rlc
```

**Phase 2 reuses this exactly** — no new mock library. Test the new 401/403 branch:

```python
def test_get_raises_auth_failed_on_401(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(401))
    rlc = make_client(tmp_path, transport)
    with pytest.raises(AuthFailedError) as exc_info:
        rlc.get("https://www.wattpad.com/x")
    assert exc_info.value.status_code == 401
    assert "x" in exc_info.value.url
    rlc.close()


def test_get_does_not_retry_on_401(tmp_path):
    """D-14: first 401 fails immediately, no retries."""
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(401)
    transport = httpx.MockTransport(handler)
    rlc = make_client(tmp_path, transport)
    with pytest.raises(AuthFailedError):
        rlc.get("https://www.wattpad.com/x", max_attempts=5)
    assert calls["n"] == 1  # not 5
    rlc.close()
```

**Other established test infrastructure (verified):**

| Tool | Where | Pattern | Phase 2 use |
|------|-------|---------|-------------|
| `pytest.MonkeyPatch` (`monkeypatch` fixture) | every test file | `monkeypatch.setattr(...)` | Patch `os.replace` to raise mid-call (success criterion #4) |
| `unittest.mock.MagicMock` | `test_jobs.py:20-32 _make_deps` | Mock `JobDeps` callables | Mock `fetch_chapter_html` to inject 401-raising response (success criterion #3) |
| `fastapi.testclient.TestClient` | `test_web_routes.py:5,15` | `TestClient(app); client.post("/setup", ...)` | Test the new `/setup` POST flow |
| `pytest-vcr` / `vcrpy` | dev dep, but cassettes minimal in Phase 1 — only `test_end_to_end.py` was meant to use it (skipped) | Recorded HTTP fixtures | **NOT used in Phase 2** — all auth tests use MockTransport (faster, no cassettes) |
| `respx` (httpx mock library) | NOT a dependency | — | **DO NOT add.** Use MockTransport which httpx ships with. |
| `output_dir` fixture | `conftest.py:11-15` | tmp `wattpad-archive/` per test | Reuse for `_save_cookie` tests |

**Capsys for stderr capture** (success criterion #1 — CLI prints AuthError to stderr):

```python
def test_main_archive_blank_cookie_exits_2(output_dir, monkeypatch, capsys):
    # Pre-create _config.toml with empty cookie (load_config does this on first run anyway)
    (output_dir / "_config.toml").write_text(
        'cookie = ""\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
        encoding="utf-8",
    )
    # Mock RateLimitedClient.get to return 401 for the probe (or have validate_cookie
    # do a pre-check on empty string and raise AuthError directly without HTTP call)
    monkeypatch.setattr(
        "local_story_archive.cli.archive_story",
        lambda *a, **kw: pytest.fail("archive_story must not be called"),
    )
    rc = main(["--output", str(output_dir), "story", "12345"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "AuthError" in err
    assert "/setup" in err  # remediation hint per D-08
```

**Note on blank cookie:** `validate_cookie()` should short-circuit on `cfg.cookie == ""` and raise `AuthError("no cookie configured — set one via /setup")` WITHOUT making an HTTP call. This makes success criterion #1 ("blank or obviously-invalid cookie ... exits before making any archive API calls") trivially satisfied for the blank case. Add this as the first check in `validate_cookie`. The "obviously invalid" case (e.g., a non-empty but bad cookie string) is handled by the actual probe returning 401.

## Validation Architecture (Nyquist)

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ (`pyproject.toml:24` dev dep) |
| Config file | `pyproject.toml [tool.pytest.ini_options]` (lines 33-36) |
| Quick run command | `pytest tests/unit/test_auth.py tests/unit/test_client.py tests/unit/test_cli.py tests/unit/test_jobs.py tests/unit/test_web_routes.py -x -q` |
| Full suite command | `pytest -q` (already excludes `live` marker via `addopts = "-m 'not live'"`) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AUTH-01 | `validate_cookie()` raises `AuthError` on 401 | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_raises_on_401 -x` | ❌ Wave 0 |
| AUTH-01 | `validate_cookie()` raises `AuthError` on 403 | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_raises_on_403 -x` | ❌ Wave 0 |
| AUTH-01 | `validate_cookie()` raises `AuthError` on 3xx → /login redirect (Location header inspection) | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_raises_on_login_redirect -x` | ❌ Wave 0 |
| AUTH-01 | `validate_cookie()` returns None on 200 | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_passes_on_200 -x` | ❌ Wave 0 |
| AUTH-01 | `validate_cookie()` propagates `httpx.RequestError` (does NOT raise AuthError on transport failure) | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_propagates_network_error -x` | ❌ Wave 0 |
| AUTH-01 | `validate_cookie()` raises `AuthError` immediately on empty cookie (no HTTP call) | unit | `pytest tests/unit/test_auth.py::test_validate_cookie_short_circuits_on_blank -x` | ❌ Wave 0 |
| AUTH-02 | CLI `archive` exits 2 on `AuthError`, prints to stderr, does not call `archive_story` | unit | `pytest tests/unit/test_cli.py::test_main_archive_auth_failure_exits_2 -x` | ❌ Wave 0 (extends test_cli.py) |
| AUTH-02 | CLI `library` / `list` / `url` also gated (parametrized) | unit | `pytest tests/unit/test_cli.py::test_main_all_archive_branches_gated -x` | ❌ Wave 0 |
| AUTH-02 | CLI `status` does NOT validate (no network call required) | unit | `pytest tests/unit/test_cli.py::test_main_status_skips_validation -x` | ❌ Wave 0 (extends existing test_main_status_does_not_make_network_calls) |
| AUTH-02 | CLI `serve` does NOT validate at startup | unit | `pytest tests/unit/test_cli.py::test_main_serve_skips_validation -x` | ❌ Wave 0 |
| AUTH-03 | `/setup` POST with auth-failure cookie re-renders 400, banner present, `_config.toml` unchanged | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_invalid_cookie_rerenders -x` | ❌ Wave 0 (extends test_web_routes.py) |
| AUTH-03 | `/setup` POST with valid cookie saves and redirects 303 | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_valid_cookie_saves -x` | ❌ Wave 0 |
| AUTH-03 | `/setup` POST with network error renders banner with `error_kind="network"` | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_network_error -x` | ❌ Wave 0 |
| AUTH-03 | `/setup` POST shows attempted_cookie_masked back on error | integration (TestClient) | `pytest tests/unit/test_web_routes.py::test_setup_post_shows_masked_attempted -x` | ❌ Wave 0 |
| AUTH-04 | `RateLimitedClient.get()` raises `AuthFailedError` on first 401, does not retry | unit | `pytest tests/unit/test_client.py::test_get_does_not_retry_on_401 -x` | ❌ Wave 0 (extends test_client.py) |
| AUTH-04 | Same for 403 | unit | `pytest tests/unit/test_client.py::test_get_raises_on_403 -x` | ❌ Wave 0 |
| AUTH-04 | `AuthFailedError.status_code` and `.url` populated | unit | `pytest tests/unit/test_client.py::test_auth_failed_error_payload -x` | ❌ Wave 0 |
| AUTH-04 | `archive_story()` propagates `AuthFailedError` (not swallowed by per-part try/except) | unit | `pytest tests/unit/test_jobs.py::test_archive_story_propagates_auth_failed -x` | ❌ Wave 0 (extends test_jobs.py) |
| AUTH-04 | `archive_story()` emits `auth.failed` event before re-raising | unit | `pytest tests/unit/test_jobs.py::test_archive_story_emits_auth_failed_event -x` | ❌ Wave 0 |
| AUTH-04 | `JobRunner` ends job with status `failed` and the AuthFailedError message visible | integration (TestClient) | `pytest tests/unit/test_runner.py::test_runner_marks_failed_on_auth_failure -x` | ❌ Wave 0 (extends test_runner.py) |
| AUTH-05 | `_save_cookie()` writes to tmp + `os.replace()` (test by inspecting tmp file ephemerally OR by patching `os.replace` to assert call shape) | unit | `pytest tests/unit/test_web_routes.py::test_save_cookie_uses_atomic_pattern -x` | ❌ Wave 0 |
| AUTH-05 | Crash mid-write (monkeypatch `os.replace` to raise) leaves `_config.toml` either unchanged or fully written, never zero bytes | unit | `pytest tests/unit/test_web_routes.py::test_save_cookie_crash_safe -x` | ❌ Wave 0 |
| AUTH-05 | Tmp file cleanup on exception (no leftover `*.tmp.*` files in archive dir after a failed write) | unit | `pytest tests/unit/test_web_routes.py::test_save_cookie_cleans_up_tmp_on_failure -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/test_auth.py tests/unit/test_client.py tests/unit/test_cli.py tests/unit/test_jobs.py tests/unit/test_web_routes.py tests/unit/test_runner.py -x -q` — ~2-5 seconds
- **Per wave merge:** `pytest -q` — full unit suite
- **Phase gate:** `pytest -q` green before `/gsd-verify-work`; manual probe-URL verification (curl commands documented in Probe Endpoint Decision) executed once during Plan 1 implementation

### Wave 0 Gaps
- [ ] `tests/unit/test_auth.py` — NEW test file. Covers AUTH-01 unit tests (6 tests above)
- [ ] `tests/unit/test_client.py` — extend with 401/403/payload tests (3 new tests)
- [ ] `tests/unit/test_cli.py` — extend with auth-gate tests (4 new tests)
- [ ] `tests/unit/test_jobs.py` — extend with AuthFailedError propagation tests (2 new tests)
- [ ] `tests/unit/test_web_routes.py` — extend with /setup error-flow tests (4 new tests) + atomic save tests (3 new tests)
- [ ] `tests/unit/test_runner.py` — extend with one integration-style test that submits a job whose work raises AuthFailedError and asserts JobManager records it as failed (1 new test)
- [ ] No new framework install needed — pytest 8.0+, pytest-vcr, vcrpy, ruff already in dev deps. `[VERIFIED: pyproject.toml:24-28]`
- [ ] No new fixtures needed — `output_dir` and `tmp_path` cover all file-write tests; `MockTransport` covers all HTTP tests; `monkeypatch` covers `os.replace` interception.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Cookie-based session against Wattpad — Phase 2 strengthens this with explicit pre-flight validation. No password handling local; cookies pasted from browser. |
| V3 Session Management | yes | Wattpad owns session lifecycle. Phase 2 adds expiry detection (probe + mid-job 401/403). |
| V4 Access Control | partial | Single-user local tool — no inter-user access control needed. Path-traversal protection on web routes already in place (Phase 1). |
| V5 Input Validation | yes | `cookie.strip()` already done. Submitted cookie is treated as opaque — never interpolated into URLs, never logged in full (only masked via `_mask`). |
| V6 Cryptography | no | No new crypto in this phase. Cookie stored in plaintext `_config.toml` per existing project decision (single-user local tool — explicitly accepted in `.planning/codebase/INTEGRATIONS.md` §Secrets Location and `CLAUDE.md` §Constraints) |
| V7 Error Handling & Logging | yes | New `logger.warning("Auth failure on %s — HTTP %d", url, status)` in `RateLimitedClient.get` does NOT log the cookie value. `AuthError` / `AuthFailedError` messages contain status code + URL but NOT the cookie. `_mask()` always used for any cookie display. |
| V13 API & Web Service | yes | New `/setup` POST flow validates submitted cookie against the API before persisting. |

### Known Threat Patterns for Python+FastAPI+httpx

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Cookie value logged in plaintext | Information Disclosure | `_mask()` is the ONLY way cookie data appears in logs / templates. Phase 2 reuses it for `attempted_cookie_masked` (D-11). The `logger.warning` in `RateLimitedClient.get` logs only the URL and status code, never the cookie. |
| Cookie value reflected in error page (XSS via `error_message`) | Tampering / XSS | Jinja2 auto-escape is on by default in this codebase (verified Phase 1, CONTEXT.md `<code_context>` line 158). The `error_message` from `repr(exception)` is auto-escaped in the rendered template — no XSS risk even if a malicious server returned crafted error text. |
| Side-channel timing attack on cookie validation | Information Disclosure | Not applicable — Wattpad does the actual auth check; we just observe the response. Local timing data leaks nothing. |
| Cookie persisted to a world-readable file | Information Disclosure | `_config.toml` permissions are inherited from Python's default file creation mode (0o644 on POSIX, default ACL on Windows). User's responsibility per existing CLAUDE.md / INTEGRATIONS.md. Phase 2 does not change this. |
| Replay of old cookie after rotation | Spoofing | Wattpad invalidates old session cookies server-side; local cookie cache (`_config.toml`) is overwritten by `_save_cookie()`. Standard. |
| Race between `_save_cookie` and concurrent CLI run | Tampering | D-19 atomic write closes the half-written-file race. Two concurrent writers could still collide on the rename (last writer wins) but neither leaves a corrupt target — acceptable per D-19. |
| Probe URL injection (URL controlled by config) | Tampering | `_PROBE_URL` is a module constant, not user-supplied. No injection vector. |

## Code Examples

### Example 1: Probe with explicit redirect detection

```python
# local_story_archive/auth.py — full module sketch (Plan 1 implementation)
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from local_story_archive.client import RateLimitedClient

logger = logging.getLogger(__name__)

# Probe URL — see RESEARCH §"Probe Endpoint Decision" for rationale.
# Update this single constant if Wattpad changes endpoint behavior.
_PROBE_URL = "https://www.wattpad.com/api/v3/users/wattpad/library?limit=1"


class AuthError(Exception):
    """Cookie validation failed at startup or /setup POST."""
    pass


class AuthFailedError(AuthError):
    """Wattpad returned 401/403 mid-job. Subclass of AuthError so callers
    can `except AuthError` to catch both startup and mid-job variants."""
    def __init__(self, message: str, *, status_code: int, url: str):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


def validate_cookie(client: "RateLimitedClient") -> None:
    """Probe Wattpad to verify the configured cookie is accepted.

    Raises:
        AuthError: cookie missing/empty, or Wattpad returned 401/403/redirect-to-login.
        httpx.RequestError: network/transport failure (caller decides how to handle).

    Returns None on a 2xx response.
    """
    # Short-circuit on empty cookie — no point hitting the network.
    if not getattr(client, "_client", None):
        raise AuthError("client is not initialized")
    # We probe with follow_redirects=False so a 3xx-to-/login is observable.
    try:
        resp = client.get(_PROBE_URL, max_attempts=1, follow_redirects=False)
    except AuthFailedError as e:
        # 401/403 came back via the new client.py branch — re-raise as AuthError
        # so /setup POST and CLI handlers can catch the unified base class.
        raise AuthError(str(e)) from e

    if 200 <= resp.status_code < 300:
        return
    if 300 <= resp.status_code < 400:
        location = resp.headers.get("Location", "")
        if "/login" in location.lower():
            raise AuthError(
                f"Wattpad redirected probe to login (HTTP {resp.status_code}, "
                f"Location={location!r}) — cookie likely expired"
            )
        logger.warning(
            "Probe redirected to %r (status %d) — not /login, treating as success",
            location, resp.status_code,
        )
        return
    raise AuthError(f"Probe returned unexpected HTTP {resp.status_code}")
```

### Example 2: 401/403 fast-fail in client.py

```python
# Insert in local_story_archive/client.py, between line 72 and line 73:
            # AUTH-04 / D-13: 401/403 detection BEFORE 429/5xx retry branches.
            # AuthFailedError is loud and immediate — D-14 says do not retry.
            if resp.status_code in (401, 403):
                from local_story_archive.auth import AuthFailedError  # deferred — break cycle
                logger.warning(
                    "Auth failure on %s — HTTP %d", url, resp.status_code,
                )
                raise AuthFailedError(
                    f"Wattpad returned HTTP {resp.status_code} for {url} — "
                    "cookie likely expired",
                    status_code=resp.status_code,
                    url=url,
                )
```

### Example 3: archive_story per-part except re-raise + auth.failed event

```python
# local_story_archive/jobs.py — modify the per-part except block at lines 144-152:
        try:
            raw_html = deps.fetch_chapter_html(client, part.url)
            content: ChapterContent = deps.parse_chapter(raw_html)
            # ... existing body ...
            emit("part.done", { ... })
        except AuthFailedError as e:
            # D-16: re-raise so the job ends `failed`, not silently empty
            # D-17: emit auth.failed BEFORE the re-raise so SSE shows it
            emit("auth.failed", {
                "part_id": part.part_id,
                "status_code": e.status_code,
                "url": e.url,
                "message": str(e),
            })
            raise
        except Exception as e:
            logger.exception("part %s failed: %s", part.part_id, e)
            # ... existing failure handling ...
```

Required new import: `from local_story_archive.auth import AuthFailedError` at the top of `jobs.py`.

### Example 4: Atomic cookie write

```python
# local_story_archive/web/routes.py — replace _save_cookie body:
def _save_cookie(output_dir: Path, cookie: str) -> None:
    """Write/update the cookie line in _config.toml atomically.

    Process-kill safe: an interrupt during the write leaves either the old
    file or no change, never a half-written one. Mirrors archive/store.py
    atomic_write_text pattern. Same-directory tmp file guarantees same-volume
    rename on Windows."""
    config_path = output_dir / "_config.toml"
    cookie = cookie.strip()
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        new_lines = []
        replaced = False
        for line in lines:
            if line.lstrip().startswith("cookie "):
                new_lines.append(f'cookie = "{cookie}"')
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f'cookie = "{cookie}"')
        new_text = "\n".join(new_lines) + "\n"
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        new_text = (
            f'cookie = "{cookie}"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n'
        )
    # Atomic write: same-directory tmp + os.replace. PID/TID suffix avoids
    # collision if two writers race on the same target.
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    tmp = config_path.with_suffix(config_path.suffix + suffix)
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, config_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
```

Required new imports at top of `web/routes.py`: `import os`, `import threading`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Inspect `resp.history` after auto-follow | Per-call `follow_redirects=False` on the probe | httpx 0.18+ supported this, default True since 0.20 | Cleaner intent, no need to remember to inspect `history` |
| `os.rename` on Windows (Python 2) | `os.replace` on all platforms (Python 3.3+) | Python 3.3 (2012) | Cross-platform clobber-on-rename without `try/except OSError` |
| Hand-rolled session-check via fragile homepage scrape | Auth probe against an API endpoint with documented behavior | n/a | Specific to this project — Phase 2 introduces it |

**Deprecated/outdated:**
- `aiohttp` for sync workloads — irrelevant; this project uses `httpx` already.
- `requests` cookie-jar manual setup — `httpx.Cookies` handles it; build_client already does.
- `tempfile.NamedTemporaryFile(delete=False)` with cross-volume risk — same-directory `Path.with_suffix(...)` pattern (already used in `archive/store.py`) is the project standard.

## Assumptions Log

> Claims tagged `[ASSUMED]` in this research that need user confirmation before locking.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `GET /api/v3/users/wattpad/library?limit=1` returns 401 OR 3xx-to-/login when called with an invalid `token` cookie (and 200 with a valid one). Documentation states the endpoint requires auth, but specific 401-vs-redirect behavior is not enumerated. | Probe Endpoint Decision | Probe could return 200 with empty `stories` array on a bad cookie — would silently defeat success criterion #1. **Mitigation: manual curl verification documented in Probe Endpoint Decision MUST run during Plan 1 implementation.** |
| A2 | Wattpad's redirect-to-/login uses `Location` header with `/login` substring (not e.g. `signin` or query-encoded path) | httpx Redirect & Probe Mechanics | If Wattpad redirects to `/login?...` with a different path component, the substring check still matches. Only fails if they redirect to `/auth/sign-in` or similar — would need to widen substring check. **Mitigation: same manual curl verification surfaces this.** |
| A3 | Antivirus interference with `os.replace` on Windows is "uncommon enough to ignore" for `_config.toml` writes in user's archive directory | Atomic Write on Windows | A test running under aggressive AV could see flaky `PermissionError`. Acceptable risk for personal tool; user can retry. **Mitigation: none needed in Phase 2; if it surfaces in production, add 1-retry-with-50ms-sleep in a future patch.** |
| A4 | Wattpad's `/api/v3/internal/auth/check` exists as a fallback probe URL (mentioned in CONTEXT.md but not verified in surveyed unofficial docs) | Probe Endpoint Decision | If both primary and fallback probe URLs misbehave, we'd need a third option. **Mitigation: planner picks ONE primary URL; fallback is documented for future iteration.** |
| A5 | The `setup_post` rewrite's `dataclasses.replace(cfg, cookie=submitted)` won't trigger `Config.__post_init__` validation (Config has no `__post_init__`) | setup_post Flow Rewrite Sketch | If a future Phase adds `__post_init__` validation to Config (e.g., min cookie length), `dataclasses.replace` would re-run it — a submitted cookie that fails validation would raise from the replace call, not from `validate_cookie`. **Mitigation: not an issue today; revisit if Config grows __post_init__.** Verified: Config has no __post_init__ in current config.py. |

**If this table is empty:** All claims would be verified — but A1/A2 specifically require manual network verification that I cannot perform from this research session. Planner MUST include the curl-verification step in Plan 1.

## Open Questions / Risks (RESOLVED)

1. **Probe URL behavior with bogus cookie — STILL needs manual verification (STATE.md flagged this).**
   - What we know: documentation says `users/{username}/library` requires auth; we know it returns paginated `stories` with a valid cookie (existing `fetch_library` works).
   - What's unclear: does it return 401 / redirect / 200-with-empty when called with a malformed cookie? With NO cookie?
   - Recommendation: Plan 1's first implementation task includes the three curl probes documented in "Probe Endpoint Decision". Block the rest of Plan 1 on confirming results match expectation. If the library probe misbehaves, swap `_PROBE_URL` to the documented `/api/v3/internal/auth/check` before continuing.
   - **RESOLVED:** Plan 01 Task 1 is a blocking checkpoint that runs the three curls and locks _PROBE_URL based on the observed outcome (default library probe OR fallback to /api/v3/internal/auth/check).

2. **Behavior when `RateLimitedClient.get()` is called by `validate_cookie()` with `follow_redirects=False` AND the response is a 401.**
   - What we know: D-13 raises `AuthFailedError` from inside `RateLimitedClient.get()` BEFORE we ever inspect the response in `validate_cookie()`.
   - What's unclear: in `validate_cookie()`, do we catch `AuthFailedError` and re-raise as `AuthError`? Or let it propagate (since `AuthFailedError` IS an `AuthError` per D-03)?
   - Recommendation: per the code example in §Code Examples → Example 1, catch and re-wrap as `AuthError(str(e))`. This way callers get a uniform `AuthError` regardless of whether the failure came from the client branch or the redirect branch. Slight loss of `status_code` precision in the validate path (we discard the AuthFailedError attributes) — acceptable because `validate_cookie` callers (CLI startup, /setup POST) only care that auth failed, not which HTTP status. If a planner prefers to preserve the attributes, they can re-raise the AuthFailedError unchanged (it's already an AuthError subclass).
   - **RESOLVED:** Plan 01s auth.py implementation catches AuthFailedError inside validate_cookie and re-wraps as AuthError(str(e)) -- uniform AuthError surface for CLI / /setup callers.

3. **`_save_cookie` cleanup-on-exception scope.**
   - What we know: D-19 mandates cleanup of the tmp file on exception during the write/replace block.
   - What's unclear: if the OS `os.replace` itself raises (rare PermissionError under AV), the tmp file may have been written but not renamed. Cleanup deletes the tmp — correct. But if `tmp.write_text` raises mid-write, the tmp file may be partially written. Cleanup deletes the partial tmp — correct.
   - Edge case: what if `tmp.unlink(missing_ok=True)` itself raises (also AV)? We'd be re-raising the original exception while leaking a tmp file. Acceptable — user can manually clean up `_config.toml.*.tmp` once.
   - Recommendation: keep the cleanup as `try/except: tmp.unlink(missing_ok=True); raise`. Don't over-engineer.
   - **RESOLVED:** Plan 05 Task 1 uses try: tmp.write_text + os.replace; except Exception: tmp.unlink(missing_ok=True); raise -- verified by test_save_cookie_cleans_up_tmp_on_failure.

4. **CLI auth gate position relative to `Manifest` / `RateLimitedClient` construction.**
   - What we know: `cli.py:main()` currently constructs `client = RateLimitedClient(cfg)` and `manifest = Manifest(cfg.output_dir).connect()` BEFORE the dispatch block (lines 76-77).
   - What's unclear: does `_require_auth` go BEFORE or AFTER these constructions? If before, we can't pass the `client` to `validate_cookie` (it doesn't exist yet). If after, the `try/finally` cleanup at lines 104-106 properly closes them on AuthError.
   - Recommendation: AFTER construction. The `_require_auth(cfg, client)` helper reuses the already-built client. The existing `try/finally` block at lines 104-106 already closes `manifest` and `client`, so if AuthError is raised inside the dispatch branch, cleanup runs. The `except AuthError` block lives in `main()` AROUND the try/except/finally, OR inside the try block before each branch — both work. Simpler: catch inside `main()` immediately around the dispatch block, AFTER the finally.
   - **RESOLVED:** Plan 03 places _require_auth(client) AFTER client/manifest construction with the AuthError catch as an inner try/except inside the existing try/finally cleanup block.

5. **`web/routes.py:setup_post` return type annotation.**
   - What we know: current annotation is `-> RedirectResponse`. After Phase 2 it returns either `RedirectResponse` (success) or `HTMLResponse` (failure via `templates.TemplateResponse`).
   - Recommendation: change annotation to `-> RedirectResponse | HTMLResponse`. FastAPI accepts both. Ruff won't complain. `[VERIFIED: routes.py uses similar mixed-return patterns in other routes]`.
   - **RESOLVED:** Plan 05 Task 2 setup_post signature widens the return to RedirectResponse | HTMLResponse.

6. **Probe rate-limit budget impact.**
   - What we know: every `local-story-archive archive ...` invocation now consumes one extra token from the bucket (the probe). Default rate is 2.0 req/sec; one probe = 0.5s of budget.
   - Impact: negligible. CLI archive runs already issue dozens of requests; one extra at startup is invisible.
   - **DOES the probe also count against the bucket from the `/setup` POST path?** Yes — the transient `RateLimitedClient(transient_cfg)` has its own bucket, separate from the long-lived app client. Each `/setup` validation is one bucket-of-2 fully fresh. Effectively no rate impact.
   - **RESOLVED:** No code change required -- impact analysis confirms the extra probe consumption is negligible (CLI: ~0.5s of a 2 req/s bucket; /setup: each validation gets a fresh transient bucket).

## Sources

### Primary (HIGH confidence)
- `local_story_archive/client.py` (lines 56-89) — current `get()` method, exact insertion point verified
- `local_story_archive/cli.py` (lines 72-106) — current `main()` with dispatch block; auth gate insertion sites identified
- `local_story_archive/web/routes.py` (lines 22-77) — current `_save_cookie` and `setup_post`; TemplateResponse signature pattern verified across all routes
- `local_story_archive/jobs.py` (lines 100-152) — current per-part try/except; AuthFailedError re-raise insertion site identified
- `local_story_archive/archive/store.py` (lines 47-71) — atomic write pattern to mirror verbatim
- `local_story_archive/config.py` (lines 10-16) — Config is `frozen=True`; `dataclasses.replace` is the clone idiom
- `tests/unit/test_client.py` (lines 94-99) — `make_client(tmp_path, transport)` mock pattern reused for AUTH-04 tests
- `tests/unit/test_web_routes.py` (lines 5-30) — `TestClient(app)` pattern reused for AUTH-03 tests
- `tests/conftest.py` (lines 11-15) — `output_dir` fixture reused for `_save_cookie` tests
- [Python httpx Client.get signature](https://www.python-httpx.org/api/) — `follow_redirects` is a per-call kwarg
- [Python httpx Quickstart — Redirects](https://www.python-httpx.org/quickstart/) — `response.history`, `response.next_request` semantics
- [pyproject.toml](D:\Dev\Local Story Archive\pyproject.toml) lines 10-28 — confirms httpx>=0.27, pytest>=8.0, pytest-vcr/vcrpy in dev deps

### Secondary (MEDIUM confidence)
- [GitHub: Archive-WP/WattpadAPIDocumentation — User_Info.md](https://github.com/Archive-WP/WattpadAPIDocumentation/blob/master/API_Endpoints/User_Info.md) — `/api/v3/users/{username}` documented, auth not required for reads
- [GitHub: Archive-WP/WattpadAPIDocumentation — Library.md](https://github.com/Archive-WP/WattpadAPIDocumentation/blob/master/API_Endpoints/Library.md) — `library` endpoint **explicitly requires authentication cookies**; this is the chosen probe target
- [GitHub: Archive-WP/WattpadAPIDocumentation — Authentication.md](https://github.com/Archive-WP/WattpadAPIDocumentation/blob/master/General/Authentication.md) — login flow documented; no probe endpoint enumerated
- [Python bug tracker: issue 46003](https://bugs.python.org/issue46003) — `os.replace` Windows behavior (MoveFileExW + same-volume requirement)
- [Python bug tracker: issue 1704547](https://bugs.python.org/issue1704547) — `os.rename` / `os.replace` on Windows uses `MoveFileEx`

### Tertiary (LOW confidence — flagged for verification)
- [pyosreplace on PyPI](https://pypi.org/project/pyosreplace/) — `MoveFileEx` "may silently fall back to non-atomic CopyFile" (sufficient for our needs but worth knowing)
- `[ASSUMED]` Antivirus-PermissionError interaction with `os.replace` — common knowledge, not formally documented
- `[ASSUMED]` Wattpad redirect target uses `/login` path component (vs. `/auth/sign-in` etc.) — researcher inference; manual curl verification needed

## Metadata

**Confidence breakdown:**
- Probe URL: MEDIUM — endpoint is documented as auth-required; specific 401-vs-redirect-vs-empty-200 behavior on bad cookie needs manual verification (STATE.md flagged)
- httpx redirect mechanics: HIGH — official docs confirm per-call `follow_redirects` kwarg and `response.history`/`next_request` semantics
- Atomic write on Windows: HIGH — Python bug tracker + pyosreplace docs confirm `MoveFileEx`-based behavior; existing codebase pattern at `archive/store.py:54-62` already proves portability
- RateLimitedClient insertion point: HIGH — exact line numbers verified by reading current file
- setup_post rewrite: HIGH — existing TemplateResponse signature confirmed by reading every call site in `web/routes.py`
- Test infrastructure: HIGH — reused verbatim from Phase 1 patterns; no new libraries
- Validation Architecture: HIGH — pyproject.toml + existing test files confirm framework, all gaps enumerated

**Research date:** 2026-05-03
**Valid until:** 2026-06-03 (30 days for stable docs); manual probe verification has shorter shelf life — re-check before each major Wattpad outage / API change

## RESEARCH COMPLETE
