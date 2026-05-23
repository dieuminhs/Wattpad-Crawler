# Desktop App Packaging

This directory contains the Tauri desktop wrapper for Wattpad Crawler. The desktop app keeps the existing Python/FastAPI crawler as the backend and opens it in a native desktop window.

## Development prerequisites

- Python 3.11+
- Node.js 20+
- Rust stable
- Tauri system prerequisites for your OS

Install Python dependencies from the repository root:

```bash
python -m pip install -e ".[dev,desktop]"
```

Install the desktop tooling:

```bash
npm install
```

Run the backend by itself:

```bash
wattpad-crawler-desktop-backend
```

Run the native shell against the editable Python backend:

```bash
npm run desktop:dev
```

## Backend behavior

The desktop backend uses a native per-user archive directory by default instead of `./wattpad-archive`:

- Windows: `%LOCALAPPDATA%\Wattpad Crawler\wattpad-archive`
- macOS: `~/Library/Application Support/Wattpad Crawler/wattpad-archive`
- Linux: `$XDG_DATA_HOME/wattpad-crawler/wattpad-archive` or `~/.local/share/wattpad-crawler/wattpad-archive`

Set `WATTPAD_CRAWLER_BACKEND` to point Tauri at a custom backend executable. If that variable is not set, Tauri checks for a bundled backend next to the app, then `src-tauri/bin/`, then falls back to `wattpad-crawler-desktop-backend` on `PATH`.

## Build the backend executable

```bash
npm run desktop:backend -- --clean
```

This runs `scripts/build_desktop_backend.py`, which creates a PyInstaller one-file executable at `src-tauri/bin/wattpad-crawler-desktop-backend.exe` on Windows or `src-tauri/bin/wattpad-crawler-desktop-backend` on macOS/Linux.

## Build the desktop app

```bash
npm run desktop:build
```

The current build command first rebuilds the Python backend executable and then runs `tauri build`. The Rust launcher looks for that executable when the app starts. The desktop shell is single-instance: launching it again focuses the already-open window instead of starting another backend.



## Switching archive folders

The desktop app can point at an existing archive without copying it:

1. Open Wattpad Crawler.
2. Go to **Settings**.
3. In **Archive location**, paste the full path to the existing `wattpad-archive` folder.
4. Click **Use this archive folder**.

Close other Wattpad Crawler windows before switching. The app writes this desktop-only pointer to the native app data folder and keeps story data in the selected archive folder.

## GitHub Actions builds

The workflow at `.github/workflows/build-desktop.yml` builds installer artifacts for Windows and macOS without needing a local Mac.

It runs automatically for pushed version tags like `v0.1.0`, and it can also be started manually from GitHub:

1. Open the repository on GitHub.
2. Go to **Actions**.
3. Select **Build Desktop Installers**.
4. Click **Run workflow**.
5. Download artifacts from the completed run.

Artifacts currently produced:

- Windows x64: `.msi` and NSIS `setup.exe`.
- macOS Apple Silicon: `.dmg` and `.app`.
- macOS Intel: `.dmg` and `.app`.

Artifacts expire after 14 days. For permanent downloads, attach them to a GitHub Release.

## Installer hardening still needed

This is now a functional packaging path, but a production-grade installer still needs:

1. Tauri bundle resource configuration for placing the backend executable in the final installer layout on every OS.
2. Installer icons and application metadata.
3. Release upload automation so CI artifacts are attached to GitHub Releases.
4. OS credential storage for the Wattpad cookie in a later security pass.



