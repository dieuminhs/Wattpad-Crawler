import json
from pathlib import Path

import httpx

from local_story_archive.api.user import (
    _paginate,
    fetch_library,
    parse_current_username,
    parse_library,
    parse_list_stories,
    parse_reading_lists,
)
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import Config


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


def test_parse_library_filters_null_ids():
    raw = {"stories": [{"id": "1"}, {"id": None}, {"title": "no id"}, {"id": "2"}]}
    assert parse_library(raw) == ["1", "2"]


def test_parse_library_handles_null_stories():
    assert parse_library({"stories": None}) == []
    assert parse_library({}) == []


def test_parse_reading_lists_filters_null_ids():
    raw = {"lists": [{"id": "L1", "name": "A"}, {"id": None, "name": "B"}]}
    out = parse_reading_lists(raw)
    assert len(out) == 1
    assert out[0]["id"] == "L1"


def test_parse_list_stories():
    raw = {"stories": [{"id": "S1"}, {"id": "S2"}]}
    assert parse_list_stories(raw) == ["S1", "S2"]


def test_parse_list_stories_filters_null():
    raw = {"stories": [{"id": "S1"}, {"id": None}, "garbage"]}
    assert parse_list_stories(raw) == ["S1"]

def test_parse_current_username_prefers_username():
    assert parse_current_username({"username": "alice", "name": "Alice Display"}) == "alice"

def test_parse_current_username_falls_back_to_name():
    assert parse_current_username({"name": "alice"}) == "alice"


def _mock_client(tmp_path: Path, handler):
    cfg = Config(output_dir=tmp_path, rate_limit_per_sec=1000.0)
    rlc = RateLimitedClient(cfg)
    rlc._client = httpx.Client(transport=httpx.MockTransport(handler))
    return rlc


def test_paginate_breaks_cycle(tmp_path: Path):
    """Server returning the same nextUrl forever must not infinite-loop."""
    def handler(req):
        return httpx.Response(
            200,
            json={"stories": [{"id": "1"}], "nextUrl": "https://example.com/loop"},
        )
    client = _mock_client(tmp_path, handler)
    pages = _paginate(client, "https://example.com/loop")
    assert len(pages) == 1, "should detect cycle and stop after first page"
    client.close()


def test_paginate_caps_at_max_pages(tmp_path: Path):
    """Server returning unique-but-endless pages must hit the safety cap."""
    counter = {"n": 0}
    def handler(req):
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "stories": [{"id": str(counter["n"])}],
                "nextUrl": f"https://example.com/page/{counter['n']}",
            },
        )
    client = _mock_client(tmp_path, handler)
    pages = _paginate(client, "https://example.com/page/0")
    assert len(pages) <= 200
    client.close()


def test_fetch_library_url_encodes_username(tmp_path: Path):
    """Special chars in username must not corrupt the URL."""
    requested = {}
    def handler(req: httpx.Request):
        requested["url"] = str(req.url)
        return httpx.Response(200, json={"stories": []})
    client = _mock_client(tmp_path, handler)
    fetch_library(client, "alice?foo=bar")
    assert "users/alice%3Ffoo%3Dbar/library" in requested["url"]
    client.close()
