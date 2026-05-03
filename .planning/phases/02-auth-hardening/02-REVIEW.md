---
phase: 02-auth-hardening
reviewed: 2026-05-03T16:11:40Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - tests/unit/test_auth.py
  - tests/unit/test_cli.py
  - tests/unit/test_client.py
  - tests/unit/test_jobs.py
  - tests/unit/test_runner.py
  - tests/unit/test_web_routes.py
  - wattpad_crawler/auth.py
  - wattpad_crawler/cli.py
  - wattpad_crawler/client.py
  - wattpad_crawler/jobs.py
  - wattpad_crawler/web/routes.py
  - wattpad_crawler/web/templates/setup.html
findings:
  critical: 0
  warning: 4
  info: 4
  total: 8
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-05-03T16:11:40Z
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Phase 2 (Auth Hardening) is a well-engineered set of changes. The auth gate is correctly placed at the top of every archive branch in `cli.py`, `RateLimitedClient.get()` fast-fails on Wattpad's three known unauth signals (401, 403, and 400+`PermissionDenied`), and `archive_story()` correctly re-raises `AuthFailedError` rather than swallowing it in the broad `except`. The `_save_cookie` atomic-write pattern mirrors `archive/store.py:atomic_write_text` faithfully. Test coverage is excellent — every behavioral claim in the phase plans has a matching test.

The findings below are quality concerns, not blockers. The most important one (WR-01) is a TOML-injection / corruption gap in `_save_cookie`: a cookie value containing a literal `"` or `\` character would produce an invalid `_config.toml` file that subsequent runs cannot parse. Real Wattpad tokens don't contain these characters, but the trust boundary at `/setup` does not enforce this.

The other findings are: (WR-02) `validate_cookie` reaches into `RateLimitedClient._client.cookies` (a private attribute), creating fragile coupling; (WR-03) `_save_cookie`'s line-replace logic depends on the existing line shape (`cookie = "..."` with a space), so a hand-edited config without the space would produce a duplicated `cookie` key on save; (WR-04) the `serve` branch in `cli.py` calls `manifest.close()` / `client.close()` once before `uvicorn.run` and again in the outer `finally`, relying on close() being idempotent.

No security-critical findings. Cookie values are not logged anywhere. The masked-cookie reflection in `setup.html` uses Jinja2's default autoescape, so the masked string in `value="..."` is HTML-safe.

## Warnings

### WR-01: `_save_cookie` does not escape TOML metacharacters in cookie value

**File:** `wattpad_crawler/web/routes.py:46`, `:51`, `:56`
**Issue:** Cookie strings are written into the TOML file via raw f-string interpolation: `f'cookie = "{cookie}"'`. If a user pastes a cookie containing a double-quote, backslash, or newline (e.g., `abc"def` or `abc\nde`), the resulting `_config.toml` will be malformed TOML. On the next `load_config()` call, `tomllib.loads(raw)` raises `TOMLDecodeError` → `ConfigError`, locking the user out of both CLI and web until they manually edit the file. There is no test for non-alphanumeric cookie values; `test_setup_post_strips_whitespace` only covers leading/trailing whitespace.

In practice Wattpad's `token` cookie is a base64-ish alphanumeric string without quotes — so the user-impact probability is low. But this is a real input-validation gap at a documented trust boundary (the `/setup` form). The phase context explicitly calls out "missing input sanitization at boundaries".

**Fix:** Either validate that the submitted cookie matches an expected character class before writing, or use TOML's literal-string form (single quotes) which permits any character except a single quote, or use a real TOML emitter. Minimal fix:
```python
import string
_ALLOWED = set(string.ascii_letters + string.digits + "-_.~+/=")  # base64/url-safe

def _save_cookie(output_dir: Path, cookie: str) -> None:
    config_path = output_dir / "_config.toml"
    cookie = cookie.strip()
    if not cookie or not set(cookie).issubset(_ALLOWED):
        raise ValueError(
            "cookie contains characters that cannot be safely stored in TOML; "
            "expected base64-style token from Wattpad's `token` cookie"
        )
    # ...rest unchanged
```
Then surface the `ValueError` in `setup_post` as another `error_kind` branch (`"format"` or similar) and re-render the form.

### WR-02: `validate_cookie` reaches into `RateLimitedClient._client.cookies` (private attribute)

**File:** `wattpad_crawler/auth.py:50-53`
**Issue:** `cookie_jar = getattr(client._client, "cookies", None)` accesses a private leading-underscore attribute of `RateLimitedClient`. This couples `auth.py` to `RateLimitedClient`'s internal layout — if the field is ever renamed (e.g., to `_httpx_client` for clarity), the short-circuit silently breaks: `getattr(..., None)` returns `None`, the `if cookie_jar is not None` guard skips the check, `token = ""`, and the function correctly raises `AuthError` — so the behavior degrades gracefully but the original intent (no HTTP call on blank cookie) is lost without any test failure.

The test `test_validate_cookie_short_circuits_on_blank` catches the case where the short-circuit is removed (it monkey-patches `rlc.get` to `pytest.fail`), but it does NOT catch the case where `_client` is renamed.

**Fix:** Either add a public accessor on `RateLimitedClient` (`def has_token(self) -> bool: ...`) and use it from `auth.py`, or pass the cookie value explicitly:
```python
def validate_cookie(client: "RateLimitedClient") -> None:
    if not client.has_cookie():
        raise AuthError("no cookie configured — set one via /setup or edit _config.toml")
    # ...rest unchanged
```
Then in `client.py`:
```python
def has_cookie(self) -> bool:
    """True iff a non-empty wattpad.com `token` cookie is in the jar."""
    token = self._client.cookies.get("token", domain="wattpad.com") or ""
    return bool(token.strip())
```

### WR-03: `_save_cookie` line-replace uses fragile `startswith("cookie ")` pattern

**File:** `wattpad_crawler/web/routes.py:45`
**Issue:** The check `line.lstrip().startswith("cookie ")` requires a SPACE after `cookie`. A user (or a future code path) writing `cookie="abc"` (no space, also valid TOML) would NOT match — `replaced` stays False, and a SECOND `cookie = "..."` line gets appended. TOML's behavior on duplicate keys at the top level is to raise `TOMLDecodeError`, so the next `load_config()` blows up with a confusing parse error.

Less likely but worth noting: a multi-line TOML string with `cookie = """..."""` would also confuse this parser, but that shape isn't expected here.

**Fix:** Use a regex that tolerates optional whitespace, or parse-and-reserialize. Minimal regex fix:
```python
import re
_COOKIE_LINE_RE = re.compile(r"^\s*cookie\s*=")
# ...
for line in lines:
    if _COOKIE_LINE_RE.match(line):
        new_lines.append(f'cookie = "{cookie}"')
        replaced = True
    else:
        new_lines.append(line)
```

### WR-04: `cli.py` serve branch double-closes `manifest` and `client`

**File:** `wattpad_crawler/cli.py:118-119`, `:132-134`
**Issue:** When `args.cmd == "serve"`, the code closes `manifest` and `client` at lines 118-119, calls `uvicorn.run(...)`, returns 0 — and then the outer `finally` block (lines 132-134) closes them again. `httpx.Client.close()` and `sqlite3.Connection.close()` are both idempotent in CPython today, so this works in practice, but it relies on undocumented behavior and looks like a bug to a reader. It's also asymmetric with the other branches that don't pre-close.

**Fix:** Either restructure so the `serve` branch returns from a function that doesn't share the `finally`, or use a sentinel:
```python
elif args.cmd == "serve":
    manifest.close()
    client.close()
    manifest = None  # signal to finally
    client = None
    app = build_app(cfg)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0
# ...
finally:
    if manifest is not None:
        manifest.close()
    if client is not None:
        client.close()
```
Or — cleaner — extract the serve branch into its own function that owns its own resource lifecycle and is called before the `try` block.

## Info

### IN-01: Setup form re-uses masked cookie as input `value` (UX trap)

**File:** `wattpad_crawler/web/templates/setup.html:31`
**Issue:** `<input type="password" id="cookie" name="cookie" value="{{ attempted_cookie_masked or current_cookie_masked }}" required>` pre-fills the password field with the MASKED cookie (e.g. `AbCd…5678`). If a user lands on `/setup` from the dashboard's "update cookie" link and clicks Save without typing anything, they will submit the literal masked string `AbCd…5678` as their new cookie — which then fails validation (good), but the error banner says "cookie rejected" without explaining what just happened. The submitted-masked value is also visually indistinguishable from the prior value because the input is `type="password"`.

This isn't a security issue (Jinja2 autoescapes the attribute, the masked string is not the real cookie), but it's a confusing UX surface.

**Fix:** Don't pre-fill the password input. The masked value is already shown in the error banner ("Submitted: `AbCd…5678`") which is sufficient. Make the input always start empty:
```html
<input type="password" id="cookie" name="cookie" value="" required
       placeholder="paste your token cookie">
```
Optionally add a small note: "Currently configured: `AbCd…5678`" near the field for orientation.

### IN-02: Inline imports in `cli.py:103, 108`

**File:** `wattpad_crawler/cli.py:103`, `:108`
**Issue:** `from wattpad_crawler.api.user import fetch_library` and `fetch_list_story_ids` are imported inside `main()`. Project convention (per CLAUDE.md) is "Imports at module level, not inside functions". These imports were likely deferred for startup-cost reasons or to avoid a circular import, but the module already imports from many `wattpad_crawler` submodules at the top, so neither concern obviously applies.

This pre-dates Phase 2 (the new diff didn't introduce them), but it's adjacent to the auth-gate code and worth noting.

**Fix:** Hoist to module top:
```python
from wattpad_crawler.api.user import fetch_library, fetch_list_story_ids
```
If this introduces a circular import, leave with a comment explaining why.

### IN-03: `auth.py` redirect-handling code is dead under current `client.get()` flow

**File:** `wattpad_crawler/auth.py:76-87`
**Issue:** The `if 300 <= status < 400:` branch handles 3xx responses by checking the `Location` header. But `client.get()` is called with `follow_redirects=False` and `max_attempts=1`, then `resp.raise_for_status()` is invoked at `client.py:130` for any non-special status code (3xx falls through). `raise_for_status()` only raises for 4xx/5xx, NOT 3xx — so 3xx responses do NOT raise from `client.get()`, they return successfully. Then in `auth.py` the success branch at line 110 (`if 200 <= resp.status_code < 300:`) skips them, and they fall through to `raise AuthError(f"Probe returned unexpected HTTP {resp.status_code}")` at line 112.

So the carefully-written 3xx branch (lines 76-87) is unreachable. The 3xx-redirect test (`test_validate_cookie_raises_on_login_redirect`) passes because the fallthrough at line 112 raises AuthError — but the test asserts `"login" in msg or "redirect" in msg`, and the fallthrough message is `"Probe returned unexpected HTTP 302"`, which contains neither word. The test would fail for that reason.

Wait — actually I need to re-verify. Looking again at `client.py:130`: `resp.raise_for_status()` is only reached after the 401/403/400/429/5xx branches. For a 3xx response with `follow_redirects=False`, `raise_for_status()` does NOT raise (it only raises 4xx/5xx). So the 3xx response is returned to the caller. Then in `auth.py:110`, `if 200 <= resp.status_code < 300:` is False, so it falls to line 112 with `"Probe returned unexpected HTTP 302"` — which the test asserts must contain `"login"` or `"redirect"`. The test would fail.

If the test passes, then either (a) httpx's `raise_for_status` DOES raise on 3xx (worth verifying), or (b) something else is happening. Worth a quick test run to confirm.

**Fix:** Either confirm the test passes (and document why), or move the 3xx detection from the `except httpx.HTTPStatusError` block into the success path:
```python
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
if 200 <= resp.status_code < 300:
    return
raise AuthError(f"Probe returned unexpected HTTP {resp.status_code}")
```
And remove the now-dead 3xx branch from the `except` handler.

### IN-04: `client.py:134` uses `assert resp is not None` for type narrowing

**File:** `wattpad_crawler/client.py:134`
**Issue:** `assert resp is not None  # narrowed: loop ran at least once and resp was set` is stripped under `python -O`. In practice the assertion is true (the for-loop runs at least once because `max_attempts >= 1` is enforced at line 57-58), but if a future change breaks that invariant, the code would `AttributeError` on `resp.raise_for_status()` instead of producing a clear error.

**Fix:** Replace with a runtime check:
```python
if resp is None:
    raise RuntimeError(
        "internal error: get() exited the retry loop with no response and no exception"
    )
resp.raise_for_status()
return resp
```
Or use a `cast(httpx.Response, resp)` if the goal is purely type-checker narrowing.

---

_Reviewed: 2026-05-03T16:11:40Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
