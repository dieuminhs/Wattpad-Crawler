import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.auth import AuthError, validate_cookie
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.config import load_config
from wattpad_crawler.jobs import archive_many, archive_story, resolve_story_id
from wattpad_crawler.web.app import build_app


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wattpad-crawler",
        description="Archive Wattpad stories locally.",
    )
    p.add_argument(
        "--output", type=Path, default=Path("./wattpad-archive"),
        help="Local archive directory (default: ./wattpad-archive)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_story = sub.add_parser("story", help="Archive a single story")
    sp_story.add_argument("target", help="Story ID or URL")

    sp_lib = sub.add_parser("library", help="Archive your reading library")
    sp_lib.add_argument("--user", required=True, help="Your Wattpad username")

    sp_list = sub.add_parser("list", help="Archive a reading list")
    sp_list.add_argument("list_id", help="Reading list ID or URL")

    sp_url = sub.add_parser("url", help="Archive whatever a Wattpad URL points to")
    sp_url.add_argument("target", help="Any Wattpad URL")

    sub.add_parser("status", help="Show archive status")
    sp_serve = sub.add_parser("serve", help="Run the local web UI")
    sp_serve.add_argument("--host", default="127.0.0.1")
    sp_serve.add_argument("--port", type=int, default=8000)
    return p


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_status(manifest: Manifest) -> None:
    cur = manifest.db.execute(
        "SELECT status, COUNT(*) FROM stories GROUP BY status ORDER BY status"
    ).fetchall()
    print("Stories:")
    if not cur:
        print("  (none)")
    for status, n in cur:
        print(f"  {status:12s} {n:>5}")
    cur = manifest.db.execute(
        "SELECT status, COUNT(*) FROM parts GROUP BY status ORDER BY status"
    ).fetchall()
    print("Parts:")
    if not cur:
        print("  (none)")
    for status, n in cur:
        print(f"  {status:12s} {n:>5}")


def _require_auth(client: RateLimitedClient) -> None:
    """Validate the configured Wattpad cookie before doing any archive work.

    AUTH-02 / D-05: called at the top of each of the 4 archive branches in main().
    AUTH-02 / D-06: status and serve are exempt (no network read; web /setup covers serve).
    AUTH-02 / D-07: no opt-out flag.

    Raises AuthError on failure (caught in main and formatted to stderr + sys.exit(2)).
    """
    validate_cookie(client)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.output)
    client = RateLimitedClient(cfg)
    manifest = Manifest(cfg.output_dir).connect()
    try:
        try:
            if args.cmd == "story":
                _require_auth(client)
                sid = resolve_story_id(args.target)
                archive_story(cfg, client, manifest, sid)
            elif args.cmd == "url":
                _require_auth(client)
                sid = resolve_story_id(args.target)
                archive_story(cfg, client, manifest, sid)
            elif args.cmd == "library":
                _require_auth(client)
                from wattpad_crawler.api.user import fetch_library
                ids = fetch_library(client, args.user)
                archive_many(cfg, client, manifest, ids)
            elif args.cmd == "list":
                _require_auth(client)
                from wattpad_crawler.api.user import fetch_list_story_ids
                ids = fetch_list_story_ids(client, args.list_id)
                archive_many(cfg, client, manifest, ids)
            elif args.cmd == "status":
                # D-06: status reads local sqlite only — no validation.
                _print_status(manifest)
            elif args.cmd == "serve":
                # D-06: web /setup handles auth interactively.
                # serve owns its own client/manifest lifecycle inside JobRunner threads;
                # close the ones main() opened so we don't leak them.
                manifest.close()
                client.close()
                app = build_app(cfg)
                uvicorn.run(app, host=args.host, port=args.port, log_level="info")
                return 0
            return 0
        except AuthError as e:
            # D-08: print to stderr, no traceback noise, exit 2 (= misuse / config error).
            print(
                f"AuthError: {e}\n"
                f"Update your cookie via /setup or edit {cfg.output_dir}/_config.toml.",
                file=sys.stderr,
            )
            return 2
    finally:
        manifest.close()
        client.close()


if __name__ == "__main__":
    sys.exit(main())
