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

The current build command first rebuilds the Python backend executable and then runs `tauri build`. The Rust launcher looks for that executable when the app starts.

## Installer hardening still needed

This is now a functional packaging path, but a production-grade installer still needs:

1. Tauri bundle resource configuration for placing the backend executable in the final installer layout on every OS.
2. Installer icons and application metadata.
3. CI builds on Windows, macOS, and Linux to catch platform-specific bundling differences.
4. OS credential storage for the Wattpad cookie in a later security pass.
