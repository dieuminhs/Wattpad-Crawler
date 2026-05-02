import json
from pathlib import Path

from wattpad_crawler.api.comments import parse_comments_page


def test_parse_comments_page(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "api_responses/comments_page.json").read_text())
    comments, next_url = parse_comments_page(raw)
    assert len(comments) == 2
    assert comments[0].comment_id == "c1"
    assert comments[0].user == "bob"
    assert comments[0].paragraph_id == "p_42"
    assert len(comments[0].replies) == 1
    assert comments[0].replies[0].user == "alice"
    assert next_url is None
