# Local Story Archive — Design Spec

**Date:** 2026-05-02
**Status:** Approved (pending user spec review)

## 1. Purpose

Preserve a user's Wattpad library, reading lists, favorites, and individual stories — including chapter text, inline images, all metadata, and all comments (inline + end-of-part) — to a local archive before Wattpad's takedown wave removes them.

The archive must be:

- **Self-contained** — readable without the tool (open the folder, browse files).
- **Append-only** — the tool never deletes local files, even if the remote story is gone.
- **Fast to re-run** — incremental updates only fetch deltas.

## 2. Locked-in Decisions

| Decision | Choice | Source |
|---|---|---|
| Input scope | Library, reading lists, favorites, story URLs (all supported) | Q1 |
| Output formats | EPUB **and** plain text **and** standalone HTML (all three) | Q2 |
| Authentication | Cookie paste — user pastes session cookie into local config | Q3 |
| Metadata depth | Rich — always include all comments (inline + end-of-part) | Q4 |
| Language | Python 3.11+ | Q5 |
| Update behavior | Configurable; default = incremental update; `--force` and `--skip-existing` flags available; **archive is append-only** | Q6 |
| User interface | Local web UI **and** CLI, both backed by the same core | Q7 |
| Implementation approach | Hybrid: Wattpad JSON API for metadata/comments/discovery; HTML for chapter body text (preserves formatting + inline images) | Approaches |

## 3. Architecture

### 3.1 Module layout

```
local_story_archive/
├── cli.py              # argparse entry; subcommands: library, list, story, url, status
├── config.py           # loads <output-dir>/_config.toml (cookie, paths, rate limits)
├── client.py           # HTTP session: cookie auth, rate-limit, retry, user-agent
├── api/
│   ├── user.py         # user library, reading lists, favorites
│   ├── story.py        # story metadata, chapter list
│   ├── parts.py        # chapter (part) metadata via JSON API
│   └── comments.py     # inline + end-of-part comment pagination
├── scrape/
│   └── chapter_html.py # ONLY html-parsing module; extracts body text + images
├── archive/
│   ├── store.py        # append-only filesystem layout, idempotent atomic writes
│   ├── state.py        # SQLite manifest: what's downloaded, what's pending, hashes
│   └── resume.py       # resume planner: diffs manifest vs remote, queues work
├── render/
│   ├── txt.py
│   ├── html.py
│   └── epub.py         # EbookLib
├── web/
│   ├── app.py          # FastAPI app + Jinja2 templates + HTMX
│   ├── routes.py       # /, /setup, /jobs, /jobs/{id}/stream (SSE), /library, /read/...
│   ├── templates/      # 5 templates: setup, dashboard, job, library, reader
│   └── static/         # one css file, no build step
├── jobs.py             # shared job runner: both CLI and web invoke this
└── tests/
    ├── unit/
    └── integration/    # vcrpy cassettes
```

### 3.2 Module boundaries

- `client.py` is the **only** module that touches the network.
- `scrape/chapter_html.py` is the **only** module that parses HTML. When Wattpad redesigns, only this module breaks.
- `archive/state.py` is the source of truth for "what do I have / what's still missing." Other modules are stateless w.r.t. progress.
- `render/*` reads from the local archive only — re-rendering never hits the network.
- `jobs.py` is invoked by both `cli.py` and `web/routes.py`; this is how we guarantee the CLI and web UI behave identically.

## 4. Data Flow

```
CLI command / Web action     →  Discovery                  →  Story queue
─────────────────────────────────────────────────────────────────────────
library <username>           →  api.user.library()         ─┐
list <list-id-or-url>        →  api.user.reading_list()    ─┼→  [story_id, ...]
story <story-id-or-url>      →  (direct)                   ─┤
url <any-wattpad-url>        →  resolve to story_id        ─┘
```

For each `story_id`:

```
story_id
   │
   ▼
api.story.metadata(id)              →  title, author, tags, parts[], cover
   │
   ▼
archive.state.diff(local, remote)   →  list of parts to fetch / skip / update
   │
   ▼
for each part needed:
   ├─ scrape.chapter_html(part_url)    →  body text + inline images
   ├─ api.comments.inline(part_id)     →  paginated inline comments
   ├─ api.comments.end(part_id)        →  paginated end-of-part comments
   └─ archive.store.write_part(...)    →  atomic write + manifest update
   │
   ▼
render.{txt,html,epub}(story_dir)   →  build all three output formats
   │
   ▼
archive.state.mark_complete(story_id, run_id)
```

### 4.1 Concurrency

- Stories processed **sequentially** (predictable pacing, simple resume).
- Within a story, chapter fetches run in a small worker pool (default 3).
- Global token-bucket rate limiter in `client.py` (default ~2 req/sec, configurable in `_config.toml`).

### 4.2 Atomicity

Every file write goes to `<file>.tmp`, then `os.replace()`. The SQLite manifest only records "done" *after* the replace succeeds. Interrupted runs never leave half-written files.

## 5. Output Directory Layout

```
<output-dir>/                                   ← default: ./wattpad-archive
├── _state.sqlite                               ← manifest (append-only cache)
├── _config.toml                                ← cookie path + crawler settings
├── _logs/
│   └── 2026-05-02T14-30-12.log
└── stories/
    └── <author-username>/
        └── <story-id>_<slugified-title>/       ← e.g. 123456789_shadow-and-bone-rewrite
            ├── metadata.json                   ← canonical story info
            ├── cover.jpg
            ├── parts/
            │   ├── 01_001234567_chapter-one.json
            │   ├── 01_001234567_chapter-one.html
            │   ├── 01_001234567_chapter-one.txt
            │   ├── 01_001234567_comments-inline.json
            │   ├── 01_001234567_comments-end.json
            │   └── ...
            ├── images/
            │   └── <hash>.jpg
            └── output/
                ├── <slug>.epub
                └── <slug>.html
```

### 5.1 Layout rationale

- **Author/story nesting** — easy to spot-check, easy to back up one author's work.
- **`<id>_<slug>` naming** — story ID is the durable key (slugs can change), human name is right there.
- **Numbered chapter files (`01_`, `02_`)** — alphabetical sort = reading order.
- **Three formats per chapter saved** (json/html/txt); EPUB and standalone HTML rebuilt from those on each run.
- **`metadata.json` per story** — the local archive is fully reconstructable from these files alone if `_state.sqlite` is lost (manifest is a fast cache, not authoritative).

## 6. Web UI

### 6.1 Screens

1. **Setup** (`/setup`) — paste cookie, set output dir, save. Shown on first run.
2. **Dashboard** (`/`) — three big buttons: *Grab my library*, *Grab a reading list*, *Grab one story*; URL/username input field; running and recent jobs.
3. **Job view** (`/jobs/{id}`) — live progress via Server-Sent Events: current story, chapter X of Y, comments fetched, errors. No page refresh.
4. **Library** (`/library`) — grid of downloaded story covers; filter by author/tag; click a story to read.
5. **Reader** (`/read/{story}/{chapter}`) — clean reading view of the local copy; comments expandable in a side panel.

### 6.2 Tech choices

- **FastAPI** — async fits the scraper's I/O profile.
- **Jinja2 + HTMX** — zero build step, no React/npm.
- **SSE** for live progress — simpler than websockets, perfect fit for one-way status streams.
- **No frontend build pipeline.** One CSS file, minimal vanilla JS.

Estimated UI code: ≈ 600–800 lines.

## 7. Error Handling & Resume

### 7.1 Failure policy

| Failure | Policy |
|---|---|
| Network timeout / 5xx | Exponential backoff (1, 2, 4, 8, 16s — 5 attempts), then mark `failed`, continue. |
| 429 rate-limited | Honor `Retry-After` header; otherwise back off 60s, then 120s. Pause global rate limiter. |
| Auth expired mid-run | Stop job; UI surfaces "cookie expired" with link to setup; manifest intact. |
| Story 404 (removed mid-run) | Mark story `gone` with timestamp. **Never delete local files.** Future runs skip. |
| Story moved to private (401/403) | Mark `private`, skip in future runs unless `--retry-private`. |
| Parse error in chapter HTML | Save raw HTML anyway; log; mark chapter `body_text_failed`. Comments still fetched. |
| Disk full / IO error | Stop immediately; raise loudly. Atomic writes guarantee no corruption. |
| Process killed / power loss | Next run resumes from manifest. Atomic writes guarantee no half-files. |

### 7.2 Manifest schema (`_state.sqlite`)

```
stories(story_id, author_username, status, last_remote_update, last_local_update, ...)
parts(story_id, part_id, ordinal, status, body_hash, comments_inline_done, comments_end_done, ...)
runs(run_id, started_at, ended_at, summary_json)
```

**Status values:** `pending`, `in_progress`, `done`, `failed`, `gone`, `private`, `body_text_failed`.

### 7.3 Resume logic

Default = incremental:

1. Read manifest; find anything not `done` / `gone` / `private`.
2. For `done` stories, re-check remote `last_modified` and `parts_count`. If changed, re-queue affected parts only.
3. Retries operate at the smallest unit (a comments page, not the whole story).

### 7.4 User-visible error surfaces

- **Web UI** — per-story status badges (✓ done, ⟳ in progress, ⚠ partial, ✗ failed, 🚫 gone). Click failed → last error + "retry just this one" button.
- **CLI** — `local-story-archive status` shows the same data.
- **Logs** — `_logs/<timestamp>.log`, every error tagged with `story_id` and `part_id` for grep.

## 8. Testing

### 8.1 Test layers

1. **Unit** — pure logic, no I/O. Manifest diff, renderers, parsers, API response parsing. Milliseconds.
2. **Integration** — recorded `vcrpy` cassettes in `tests/fixtures/cassettes/*.yaml`. Cassettes committed; CI runs offline.
3. **Live smoke** — one real public story, marked `@pytest.mark.live`, skipped by default. Run manually before releases.
4. **UI** — FastAPI `TestClient` against fake job runner. One optional Playwright smoke test for dashboard → start job → progress flow.

### 8.2 Coverage targets

- Non-UI modules (`api/`, `archive/`, `client.py`, `config.py`, `jobs.py`, `render/`, `scrape/`): ~90% line; 100% on parsing/rendering.
- `web/` routes: ~70% — happy paths + auth-expired path.
- No coverage requirement on `cli.py` (thin argparse layer).

### 8.3 Committed fixtures

```
tests/fixtures/
├── cassettes/
│   ├── library_small_user.yaml
│   ├── story_1_chapter.yaml
│   ├── story_with_comments.yaml
│   └── story_404.yaml              # gone-mid-run case
├── html_chapters/
│   ├── chapter_with_images.html
│   ├── chapter_with_formatting.html
│   └── chapter_minimal.html
└── api_responses/
    ├── library.json
    ├── story_metadata.json
    └── comments_paginated.json
```

### 8.4 Privacy in fixtures

Recording tool runs a regex filter to scrub `Cookie:`, `Authorization:`, and the session token before any cassette is committed. Re-recording is a manual `pytest --record-mode=rewrite` against a real account (rare, only when API drifts).

## 9. Non-Goals

To keep scope tight, the following are **out of scope** for v1:

- Multi-user / multi-account support (single user per install).
- Cloud storage / S3 sync (local filesystem only).
- Full-text search across the archive (browse via library page only).
- Re-uploading or republishing stories anywhere.
- Mobile UI (responsive desktop browser only).
- Username/password auth, keyring integration (cookie-only — Q3).
- Comment threading visualization (comments saved as JSON; basic rendering only).

## 10. Open Risks

| Risk | Mitigation |
|---|---|
| Wattpad changes JSON API endpoints | Version-pinned client; one-module-per-endpoint isolation; live smoke test catches drift early. |
| Wattpad redesigns story page HTML | Only `scrape/chapter_html.py` is affected. Fix in one place. |
| Account flagged for too-fast scraping | Conservative default rate limit (~2 req/sec); honor `Retry-After`; sequential story processing. |
| Cookie expires mid-long-run | Manifest is durable across cookie refresh; resume picks up exactly where it stopped. |
| Disk fills up on a huge library with all comments | Pre-flight estimate ("~2.3 GB for 100 stories"); `--max-size` flag stops at threshold. |
| Wattpad's takedown wave is faster than the crawler | Sequential ordering by `priority` (favorites first, then library, then lists); user can reorder via `--prioritize <list>` flag. |
