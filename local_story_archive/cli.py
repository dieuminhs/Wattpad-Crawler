import argparse
import logging
import sys
from pathlib import Path

import httpx
import uvicorn

from local_story_archive.archive.compact import compact_archive
from local_story_archive.archive.state import Manifest
from local_story_archive.auth import AuthError, validate_cookie
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import ConfigError, load_config
from local_story_archive.jobs import (
    ResolveError,
    archive_many,
    archive_story,
    resolve_story_id,
    resolve_url_story_id,
)
from local_story_archive.web.app import build_app



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local-story-archive",
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

    sp_reset = sub.add_parser("reset", help="Reset a story so the next archive refetches it")
    sp_reset.add_argument("target", help="Numeric story ID")

    sp_compact = sub.add_parser("compact", help="Remove regenerable duplicate archive files")
    sp_compact.add_argument(
        "--apply",
        action="store_true",
        help="Delete redundant files. Default is a dry run.",
    )
    sp_compact.add_argument(
        "--show-files",
        action="store_true",
        help="Print every file that would be removed.",
    )

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
    """Validate the configured Wattpad cookie before auth-only archive work.

    Direct story/url archival intentionally skips this probe; the story request
    itself can be public, and auth failures still surface through RateLimitedClient
    if Wattpad rejects the real fetch.
    AUTH-02 / D-05: called at the top of collection archive branches in main().
    AUTH-02 / D-06: status and serve are exempt (no network read; web /setup covers serve).
    AUTH-02 / D-07: no opt-out flag.

    Raises AuthError on failure (caught in main and formatted to stderr + sys.exit(2)).
    """
    validate_cookie(client)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = load_config(args.output)
    except ConfigError as e:
        print(f"ConfigError: {e}", file=sys.stderr)
        return 2
    client = RateLimitedClient(cfg)
    manifest = Manifest(cfg.output_dir).connect()
    try:
        try:
            if args.cmd == "story":
                sid = resolve_story_id(args.target)
                archive_story(cfg, client, manifest, sid)
            elif args.cmd == "url":
                sid = resolve_url_story_id(client, args.target)
                archive_story(cfg, client, manifest, sid)
            elif args.cmd == "library":
                _require_auth(client)
                from local_story_archive.api.user import fetch_library
                ids = fetch_library(client, args.user)
                archive_many(cfg, client, manifest, ids)
            elif args.cmd == "list":
                _require_auth(client)
                from local_story_archive.api.user import fetch_list_story_ids
                ids = fetch_list_story_ids(client, args.list_id)
                archive_many(cfg, client, manifest, ids)
            elif args.cmd == "status":
                # D-06: status reads local sqlite only — no validation.
                _print_status(manifest)
            elif args.cmd == "reset":
                sid = resolve_story_id(args.target)
                if not manifest.reset_story(sid):
                    print(f"Story not found: {sid}", file=sys.stderr)
                    return 2
                print(f"Reset story {sid} for refetch")
            elif args.cmd == "compact":
                result = compact_archive(cfg.output_dir, dry_run=not args.apply)
                action = "Would remove" if not args.apply else "Removed"
                mb = result.bytes_removed / 1024 / 1024
                db_mb = result.db_bytes_removed / 1024 / 1024
                print(f"{action} {result.files_removed} files and {mb:.2f} MiB total")
                if result.db_bytes_removed:
                    print(f"Database compaction accounts for {db_mb:.2f} MiB")
                if result.files_skipped:
                    print(f"Skipped {result.files_skipped} story directories without database data")
                if args.show_files:
                    for path in result.planned_paths:
                        print(path)
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
        except ResolveError as e:
            print(f"ResolveError: {e}", file=sys.stderr)
            return 2
        except httpx.HTTPStatusError as e:
            response = e.response
            print(
                f"HTTPError: Wattpad returned HTTP {response.status_code} for {response.url}",
                file=sys.stderr,
            )
            return 2
        except httpx.RequestError as e:
            print(
                f"NetworkError: Could not reach Wattpad: {e}\n"
                "Check your connection, VPN/proxy/firewall, or try again later.",
                file=sys.stderr,
            )
            return 2
    finally:
        manifest.close()
        client.close()


if __name__ == "__main__":
    sys.exit(main())
