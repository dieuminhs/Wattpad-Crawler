import json
from pathlib import Path

from wattpad_crawler.api.user import parse_library, parse_reading_lists


def test_parse_library_returns_story_ids(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/library.json").read_text())
    ids = parse_library(raw)
    assert ids == ["111", "222", "333"]


def test_parse_reading_lists(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/reading_lists.json").read_text())
    lists = parse_reading_lists(raw)
    assert len(lists) == 2
    assert lists[0]["id"] == "L1"
    assert lists[0]["name"] == "Favorites"
