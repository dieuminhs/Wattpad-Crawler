from typing import Any

from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.models import Part, Story

STORY_FIELDS = (
    "id,title,user,description,cover,tags,modifyDate,voteCount,readCount,completed,"
    "parts(id,title,url,modifyDate)"
)
STORY_URL = "https://www.wattpad.com/api/v3/stories/{story_id}?fields=" + STORY_FIELDS


def parse_story(raw: dict[str, Any]) -> Story:
    parts = [
        Part(
            part_id=str(p["id"]),
            ordinal=i + 1,
            title=p.get("title", ""),
            url=p.get("url", ""),
            last_modified=p.get("modifyDate"),
        )
        for i, p in enumerate(raw.get("parts", []))
    ]
    return Story(
        story_id=str(raw["id"]),
        title=raw.get("title", ""),
        author_username=raw.get("user", {}).get("name", ""),
        description=raw.get("description", ""),
        cover_url=raw.get("cover", ""),
        tags=list(raw.get("tags", [])),
        parts=parts,
        last_modified=raw.get("modifyDate"),
        votes=int(raw.get("voteCount", 0)),
        reads=int(raw.get("readCount", 0)),
        completed=bool(raw.get("completed", False)),
    )


def fetch_story(client: RateLimitedClient, story_id: str) -> Story:
    resp = client.get(STORY_URL.format(story_id=story_id))
    return parse_story(resp.json())
