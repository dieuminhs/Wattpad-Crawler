import json
from pathlib import Path

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
