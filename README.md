# Wattpad Crawler

Archive Wattpad stories — chapters, inline images, and all comments — to a local
append-only folder before they get removed.

## Install

```bash
git clone <this-repo>
cd "Wattpad Crawler"
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Windows (Bash):        source .venv/Scripts/activate
# Linux / macOS:         source .venv/bin/activate
pip install -e .
```

Requires Python 3.11+.

## Setup (one time)

1. Run the tool once to create a default config:
   ```bash
   wattpad-crawler --output ./wattpad-archive status
   ```
2. Open `./wattpad-archive/_config.toml` in a text editor.
3. Get your Wattpad session cookie:
   - Log in to Wattpad in your browser.
   - Open DevTools → Application/Storage → Cookies → `https://www.wattpad.com`.
   - Copy the value of the `token` cookie.
4. Paste it into `_config.toml` as `cookie = "..."`.
5. (Optional) Adjust `rate_limit_per_sec` (default 2.0) if you want to be politer to Wattpad's servers.

## Usage

```bash
# Archive everything in your library
wattpad-crawler library --user yourusername

# Archive a reading list (by ID — see lists in your Wattpad profile URL)
wattpad-crawler list <list-id>

# Archive a single story
wattpad-crawler story 123456789
wattpad-crawler url https://www.wattpad.com/story/123456-some-title

# Show status of what's been archived
wattpad-crawler status

# Verbose logging (shows every fetch)
wattpad-crawler -v library --user yourusername
```

Re-running is safe and incremental — already-downloaded chapters are skipped, only
new or failed parts are fetched. The local archive is **append-only**: the tool
never deletes a file, even if the remote story is removed from Wattpad.

## Web UI

For a friendlier experience, run the local web UI:

```bash
wattpad-crawler --output ./wattpad-archive serve
```

Then open <http://127.0.0.1:8000> in your browser. Features:

- **Setup:** paste your cookie, save (no terminal needed for this).
- **Dashboard:** click a button to archive your library, a reading list, or a single story.
- **Live progress:** watch chapters and comments stream in via Server-Sent Events.
- **Library:** browse archived stories by cover.
- **Reader:** read chapters in a clean view directly from your local archive.

The web UI calls the same code as the CLI — `_state.sqlite` is the single source of truth for both.

To bind to all interfaces (e.g. for a homelab):

```bash
wattpad-crawler serve --host 127.0.0.1 --port 8081
```

## Output Layout

```
wattpad-archive/
├── _state.sqlite           # manifest (cache; reconstructable from files)
├── _config.toml            # your settings
└── stories/<author>/<story_id>_<slug>/
    ├── metadata.json
    ├── cover.jpg
    ├── parts/
    │   ├── 01_<part_id>_<slug>.json    # canonical chapter data
    │   ├── 01_<part_id>_<slug>.html    # original Wattpad HTML
    │   ├── 01_<part_id>_<slug>.txt     # plain text
    │   ├── 01_<part_id>_comments-inline.json
    │   └── 01_<part_id>_comments-end.json
    └── output/
        ├── <slug>.epub
        ├── <slug>.html
        └── <slug>.txt
```

## What gets archived

- **Story metadata:** title, author, description, tags, vote/read counts, completion status
- **Cover image** (if present)
- **All chapters:** raw HTML, plain text extract, structured JSON with paragraph IDs
- **All comments:** inline (paragraph-attached) and end-of-chapter, with reply threads
- **Final output:** EPUB, standalone HTML, and concatenated TXT, all rebuilt on each run

## Notes & limitations

- Wattpad's API is unofficial. The tool may break if Wattpad changes endpoints or HTML structure.
- The session cookie expires periodically. Refresh it from your browser when login-required content stops working.
- Comments fetching adds significant time — popular stories can have thousands. Be patient on the first run.
- Atomic writes prevent corruption from process kill or Ctrl-C, but power loss may still leave the latest write incomplete.

## Development

```bash
pip install -e ".[dev]"
pytest                    # full unit suite
pytest -v                 # verbose
ruff check wattpad_crawler tests
```

The integration test is skipped by default and requires a vcrpy cassette to be recorded against a real Wattpad public story (instructions in `tests/integration/test_end_to_end.py`).
