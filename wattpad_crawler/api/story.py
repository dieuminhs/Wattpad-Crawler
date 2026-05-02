from typing import Any

from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.models import Part, Story

STORY_FIELDS = (
    "id,title,user,description,cover,tags,modifyDate,voteCount,readCount,completed,"
    "parts(id,title,url,modifyDate)"
)
STORY_URL = "https://www.wattpad.com/api/v3/stories/{story_id}?fields=" + STORY_FIELDS


def parse_story(raw: dict[str, Any]) -> Story:
    story_id_raw = raw.get("id")
    if story_id_raw is None:
        raise ValueError(f"Story response missing 'id': keys={list(raw.keys())}")

    user_obj = raw.get("user")
    author_username = (
        user_obj.get("name", "") if isinstance(user_obj, dict) else ""
    )

    parts: list[Part] = []
    for i, p in enumerate(raw.get("parts") or []):
        part_id_raw = p.get("id")
        if part_id_raw is None:
            raise ValueError(
                f"Part at ordinal {i + 1} missing 'id' in story {story_id_raw}"
            )
        parts.append(
            Part(
                part_id=str(part_id_raw),
                ordinal=i + 1,
                title=p.get("title", ""),
                url=p.get("url", ""),
                last_modified=p.get("modifyDate"),
            )
        )

    return Story(
        story_id=str(story_id_raw),
        title=raw.get("title", ""),
        author_username=author_username,
        description=raw.get("description") or "",
        cover_url=raw.get("cover") or "",
        tags=list(raw.get("tags") or []),
        parts=parts,
        last_modified=raw.get("modifyDate"),
        votes=int(raw.get("voteCount") or 0),
        reads=int(raw.get("readCount") or 0),
        completed=bool(raw.get("completed") or False),
    )


def fetch_story(client: RateLimitedClient, story_id: str) -> Story:
    resp = client.get(STORY_URL.format(story_id=story_id))
    return parse_story(resp.json())
