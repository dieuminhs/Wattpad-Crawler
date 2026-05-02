import json
from pathlib import Path

import pytest

from wattpad_crawler.api.story import parse_story


def test_parse_story_basic(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/story_metadata.json").read_text())
    s = parse_story(raw)
    assert s.story_id == "123456789"
    assert s.title == "Shadow & Bone Rewrite"
    assert s.author_username == "alice"
    assert s.tags == ["grishaverse", "fantasy"]
    assert s.votes == 1234
    assert s.reads == 56789
    assert s.completed is False
    assert len(s.parts) == 2
    assert s.parts[0].part_id == "1001"
    assert s.parts[0].ordinal == 1
    assert s.parts[1].ordinal == 2


def test_parse_story_handles_null_user():
    raw = {"id": "1", "title": "T", "user": None, "parts": []}
    s = parse_story(raw)
    assert s.author_username == ""


def test_parse_story_handles_string_user():
    """Some legacy responses have user as a string."""
    raw = {"id": "1", "title": "T", "user": "alice", "parts": []}
    s = parse_story(raw)
    assert s.author_username == ""


def test_parse_story_handles_null_tags():
    raw = {"id": "1", "title": "T", "user": {"name": "a"}, "tags": None, "parts": []}
    s = parse_story(raw)
    assert s.tags == []


def test_parse_story_handles_null_counts():
    raw = {
        "id": "1",
        "title": "T",
        "user": {"name": "a"},
        "voteCount": None,
        "readCount": None,
        "parts": [],
    }
    s = parse_story(raw)
    assert s.votes == 0
    assert s.reads == 0


def test_parse_story_raises_on_missing_id():
    with pytest.raises(ValueError, match="missing 'id'"):
        parse_story({"title": "T", "user": {"name": "a"}})


def test_parse_story_raises_on_missing_part_id():
    raw = {
        "id": "1",
        "title": "T",
        "user": {"name": "a"},
        "parts": [{"title": "no id here"}],
    }
    with pytest.raises(ValueError, match="missing 'id'"):
        parse_story(raw)


def test_parse_story_empty_parts_list():
    raw = {"id": "1", "title": "T", "user": {"name": "a"}, "parts": []}
    s = parse_story(raw)
    assert s.parts == []


def test_parse_story_missing_optional_fields():
    raw = {"id": "1", "title": "T", "user": {"name": "a"}}
    s = parse_story(raw)
    assert s.description == ""
    assert s.cover_url == ""
    assert s.tags == []
    assert s.parts == []
    assert s.votes == 0
    assert s.reads == 0
    assert s.completed is False
    assert s.last_modified is None
