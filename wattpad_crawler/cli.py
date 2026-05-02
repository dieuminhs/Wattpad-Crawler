import argparse
from pathlib import Path


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
