from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

import uvicorn

from wattpad_crawler.config import ConfigError, load_config
from wattpad_crawler.web.app import build_app

APP_NAME = "Wattpad Crawler"


def default_archive_dir() -> Path:
    """Return the native per-user archive directory for desktop installs."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME / "wattpad-archive"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME / "wattpad-archive"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "wattpad-crawler" / "wattpad-archive"
    return Path.home() / ".local" / "share" / "wattpad-crawler" / "wattpad-archive"


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wattpad-crawler-desktop-backend",
        description="Run the Wattpad Crawler backend for the desktop app.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_archive_dir(),
        help="Local archive directory for the desktop app.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 chooses a free local port.")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port = args.port or _free_local_port()
    try:
        cfg = load_config(args.output)
    except ConfigError as exc:
        print(f"ConfigError: {exc}", file=sys.stderr)
        return 2

    app = build_app(cfg)
    print(f"WATTPAD_CRAWLER_DESKTOP_URL=http://{args.host}:{port}", flush=True)
    uvicorn.run(app, host=args.host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
