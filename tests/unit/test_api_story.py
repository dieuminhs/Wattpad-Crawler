import json
from pathlib import Path

import pytest

from local_story_archive.api.story import (
    fetch_story,
    parse_part_page_story_id,
    parse_part_story_id,
    parse_story,
)

class FakeResponse:
    def __init__(
        self,
        text: str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)

class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response

    def get(self, url: str):
        return self.response


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

def test_fetch_story_raises_clear_error_on_non_json_response():
    client = FakeClient(FakeResponse("", headers={"content-type": "text/html"}))

    with pytest.raises(ValueError, match="non-JSON story response.*<empty response>"):
        fetch_story(client, "392287247")


def test_parse_part_story_id_prefers_group_id():
    assert parse_part_story_id({"id": 1529869290, "groupId": "123456789"}) == "123456789"


def test_parse_part_story_id_falls_back_to_group_object():
    assert parse_part_story_id({"id": 1529869290, "group": {"id": 123456789}}) == "123456789"


def test_parse_part_story_id_raises_on_missing_parent_story():
    with pytest.raises(ValueError, match="parent story"):
        parse_part_story_id({"id": 1529869290})


def test_parse_part_page_story_id_reads_embedded_story_link():
    html = '<meta property="og:url" content="https://www.wattpad.com/story/383728013-title">'
    assert parse_part_page_story_id(html) == "383728013"


def test_parse_part_page_story_id_raises_on_missing_story_link():
    with pytest.raises(ValueError, match="parent story link"):
        parse_part_page_story_id("<html><body>No story here</body></html>")
