import argparse
import logging
import sys
from pathlib import Path

from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.config import load_config
from wattpad_crawler.jobs import archive_many, archive_story, resolve_story_id


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.output)
    client = RateLimitedClient(cfg)
    manifest = Manifest(cfg.output_dir).connect()
    try:
        if args.cmd == "story":
            sid = resolve_story_id(args.target)
            archive_story(cfg, client, manifest, sid)
        elif args.cmd == "url":
            sid = resolve_story_id(args.target)
            archive_story(cfg, client, manifest, sid)
        elif args.cmd == "library":
            from wattpad_crawler.api.user import fetch_library
            ids = fetch_library(client, args.user)
            archive_many(cfg, client, manifest, ids)
        elif args.cmd == "list":
            from wattpad_crawler.api.user import fetch_list_story_ids
            ids = fetch_list_story_ids(client, args.list_id)
            archive_many(cfg, client, manifest, ids)
        elif args.cmd == "status":
            _print_status(manifest)
        return 0
    finally:
        manifest.close()
        client.close()


if __name__ == "__main__":
    sys.exit(main())
