from typing import Any
from urllib.parse import quote

from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.models import Comment

INLINE_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100"
END_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100&forms=root"
_MAX_PAGES = 200


def _parse_one(raw: dict[str, Any]) -> Comment | None:
    """Parse a single comment dict. Returns None if the comment is missing 'id'
    (defensive — Wattpad has been seen to return placeholder objects)."""
    cid = raw.get("id")
    if cid is None:
        return None
    user_obj = raw.get("user")
    user = user_obj.get("name", "") if isinstance(user_obj, dict) else ""
    replies_raw = raw.get("replies") or []
    replies = [
        c
        for c in (_parse_one(r) for r in replies_raw if isinstance(r, dict))
        if c is not None
    ]
    return Comment(
        comment_id=str(cid),
        user=user,
        body=raw.get("body") or "",
        created_at=raw.get("createdAt") or "",
        paragraph_id=raw.get("paragraphId"),
        replies=replies,
    )


def parse_comments_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    raw_comments = raw.get("comments") or []
    parsed = [
        c
        for c in (_parse_one(r) for r in raw_comments if isinstance(r, dict))
        if c is not None
    ]
    return parsed, raw.get("nextUrl")


def _fetch_all(client: RateLimitedClient, url: str) -> list[Comment]:
    out: list[Comment] = []
    seen: set[str] = set()
    pages = 0
    while url and url not in seen and pages < _MAX_PAGES:
        seen.add(url)
        pages += 1
        data = client.get(url).json()
        comments, next_url = parse_comments_page(data)
        out.extend(comments)
        url = next_url or ""
    return out


def fetch_inline_comments(client: RateLimitedClient, part_id: str) -> list[Comment]:
    return _fetch_all(client, INLINE_URL.format(part_id=quote(part_id, safe="")))


def fetch_end_comments(client: RateLimitedClient, part_id: str) -> list[Comment]:
    return _fetch_all(client, END_URL.format(part_id=quote(part_id, safe="")))
