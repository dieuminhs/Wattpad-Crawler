# Phase 2: Auth hardening - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-03
**Phase:** 02-auth-hardening
**Areas discussed:** Probe & error taxonomy, CLI validation policy, /setup error UX, Mid-job detection & propagation, Module location, Atomic write strategy

---

## Probe & Error Taxonomy (AUTH-01)

### Q1: How should validate_cookie() probe Wattpad to test the cookie?

| Option | Description | Selected |
|--------|-------------|----------|
| Single canonical endpoint | Hit GET /api/v3/users/me on every validate call (researcher confirms exact path). Simple, predictable, one place to update. | ✓ |
| Probe per-command target | For 'archive library', hit /users/{user}/library; for single story, skip validation. More complex, fewer wasted requests. | |
| Static known story | Hit a fixed public story ID. Cheapest call but doesn't actually exercise auth. | |

**Notes:** STATE.md already flags `/api/v3/users/me` for Phase 2 verification.

### Q2: What HTTP responses should validate_cookie() treat as 'auth failed'?

| Option | Description | Selected |
|--------|-------------|----------|
| Strict 401/403 only | Plus 3xx redirect-to-login. Network errors / 5xx propagate as own types. | ✓ |
| Broad: 401/403 + redirects + empty body + JSON error fields | Catches more cases but risks false positives. | |
| Configurable per call | Overkill for a personal tool. | |

### Q3: Single AuthError class or split into AuthError + AuthFailedError?

| Option | Description | Selected |
|--------|-------------|----------|
| Split: AuthError + AuthFailedError(AuthError) | Subclass for mid-job. Matches REQUIREMENTS.md wording. | ✓ |
| Single AuthError class everywhere | One class, distinguished by message. Loses structural distinction. | |

### Q4: How should validate_cookie() make the probe request?

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse RateLimitedClient with max_attempts=1 | One token per probe, no retries, single source of HTTP truth. | ✓ |
| Fresh httpx.Client built ad-hoc | Strictly isolated but duplicates cookie/UA setup. | |
| RateLimitedClient with default retries | Wastes attempts on a probe. | |

---

## CLI Validation Policy (AUTH-02)

### Q5: Should `status` (local SQLite read) skip validation alongside serve?

| Option | Description | Selected |
|--------|-------------|----------|
| Skip status too | Local-only command shouldn't depend on network. | ✓ |
| Validate status anyway | Belt-and-suspenders; one API call per status check. | |

### Q6: Where should the validation hook live in cli.py?

| Option | Description | Selected |
|--------|-------------|----------|
| Helper called inside each archive branch | _require_auth(cfg, client) called at the top of story/url/library/list. | ✓ |
| Pre-dispatch in main() guarded by allowlist | Centralized but couples dispatch and auth. | |
| Decorator/wrapper around subcommand functions | Disproportionate refactor. | |

### Q7: --skip-auth-check escape hatch?

| Option | Description | Selected |
|--------|-------------|----------|
| No opt-out flag | Validation always on for archive commands. | ✓ |
| Add --skip-auth-check flag | Useful when Wattpad is down or for debugging. | |

### Q8: Validation failure UX?

| Option | Description | Selected |
|--------|-------------|----------|
| Plain error + remediation hint, exit 2 | "AuthError: ..." to stderr; sys.exit(2). No traceback. | ✓ |
| Raise the exception, let argparse/Python show traceback | Loud but ugly for a routine cookie expiration. | |
| Plain error, exit 1 | Same as option 1 but exit code 1. | |

---

## /setup Error UX (AUTH-03)

### Q9: How should /setup show validation errors?

| Option | Description | Selected |
|--------|-------------|----------|
| Re-render setup.html in-place with error flag | Status 400, error context var. User sees error directly above form. | ✓ |
| 303 redirect to /setup?error=auth_failed | Loses pasted value. | |
| Return 400 with JSON body | Bad fit for form-driven page. | |

### Q10: Distinguish auth failures from network/transport errors?

| Option | Description | Selected |
|--------|-------------|----------|
| Distinguish 3 categories | auth (401/403) / network (httpx.RequestError) / unexpected. | ✓ |
| Single 'validation failed' message | Simpler but a flaky network looks like a bad cookie. | |
| Auth vs everything-else (2 categories) | Middle ground. | |

### Q11: What to show in the cookie input on error?

| Option | Description | Selected |
|--------|-------------|----------|
| Show the rejected cookie back, masked | _mask(submitted_value) so user can verify they pasted right one. | ✓ |
| Clear the field entirely | Annoying when failure was network-related. | |
| Preserve raw value (not masked) | Exposes cookie in HTML; violates _mask() pattern. | |

---

## Mid-Job Detection & Propagation (AUTH-04)

### Q12: Where should 401/403 detection live?

| Option | Description | Selected |
|--------|-------------|----------|
| Inside RateLimitedClient.get() | Universal coverage of all API call sites. | ✓ |
| Inside archive_story()'s per-part try/except | Local; doesn't help fetch_story or library/list fetchers. | |
| Inside each api/*.py fetcher | Distributed, error-prone. | |

### Q13: How many attempts before raising AuthFailedError?

| Option | Description | Selected |
|--------|-------------|----------|
| Fail on first 401/403 | Cookies are deterministic — retry doesn't help. | ✓ |
| Retry once with same cookie | Auth doesn't 'flap'. | |
| Apply default retry behavior | Wastes time on a known-bad cookie. | |

### Q14: How should AuthFailedError break the per-part loop in archive_story?

| Option | Description | Selected |
|--------|-------------|----------|
| Re-raise AuthFailedError inside the broad except | First line: `if isinstance(e, AuthFailedError): raise`. | ✓ |
| Add `except AuthFailedError: raise` BEFORE broad except | Cleaner Python pattern; same effect. | (acceptable alternative — planner's choice per D-16) |
| Catch and emit auth.failed and return without raising | Defeats success criterion #3. | |

### Q15: Specific 'auth.failed' progress event?

| Option | Description | Selected |
|--------|-------------|----------|
| Emit auth.failed event before re-raise | UI gets explicit auth signal in SSE stream. | ✓ |
| No specific event — rely on JobRunner's standard fail event | Simpler but loses semantic distinction. | |
| Emit a 'breaker.opened' event (mirror Phase 3) | Premature; collides with Phase 3 vocabulary. | |

---

## Module Location

### Q16: Where should auth.py live?

| Option | Description | Selected |
|--------|-------------|----------|
| wattpad_crawler/auth.py (top-level) | Peer to client.py / config.py. Cross-cutting concern. | ✓ |
| wattpad_crawler/api/auth.py | Under api/ — but auth.py exports classes used BY client.py, creates backwards layering. | |
| Split: AuthError in client.py, validate_cookie elsewhere | Two import sites. Ugly. | |

---

## Atomic Write Strategy (AUTH-05)

### Q17: Atomic write approach + helper location?

| Option | Description | Selected |
|--------|-------------|----------|
| tmp + os.replace, helper stays in routes.py | Mirror archive/store.py:_tmp_path() pattern. PID/TID suffix. | ✓ |
| tmp + os.replace, move helper into config.py | Bigger refactor; defer. | |
| tmp + os.replace, helper into auth.py | Forced fit — cookie persistence is config concern. | |

---

## Claude's Discretion

- Exact Wattpad probe endpoint URL (`/api/v3/users/me` candidate; researcher verifies)
- Redirect detection mechanics (disable follow_redirects on probe vs inspect resp.history)
- Transient client construction in setup_post (inline vs helper in auth.py)
- Test fixture shapes (mock 401/403/redirect responses; crash-during-write monkeypatch)
- Error banner styling in setup.html
- Whether to log a logger.warning on every 401/403 inside RateLimitedClient.get() before raising
- Choice between `except AuthFailedError: raise` (separate clause) or `if isinstance(e, AuthFailedError): raise` (inside broad except) — both acceptable per D-16

## Deferred Ideas

- Cookie refresh / re-login automation (AUTH-V2-01)
- OS keyring / credentials manager
- Persisting auth status to manifest (AUTH-V2-01)
- Cover-fetch auth handling (current warn-and-skip is acceptable)
- --skip-auth-check opt-out flag (rejected)
- Lifting _save_cookie() out of routes.py into config.py
- Validation retries on transient network errors
- Distinct error pages for /setup (banner-on-form chosen)
