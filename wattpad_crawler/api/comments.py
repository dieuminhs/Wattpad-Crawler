import logging
from typing import Any
from urllib.parse import quote

from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.models import Comment

logger = logging.getLogger(__name__)

INLINE_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100"
END_URL = "https://www.wattpad.com/api/v3/parts/{part_id}/comments?limit=100&forms=root"
_MAX_PAGES = 200
# REL-01 / D-11: cap nested-reply recursion to avoid RecursionError on
# malformed or adversarial Wattpad responses. Module constant rather than
# Config-exposed (D-11) — tests monkeypatch this attribute when needed.
_MAX_COMMENT_DEPTH = 10


def _parse_one(
    raw: dict[str, Any],
    depth: int = 0,
    *,
    max_depth: int = _MAX_COMMENT_DEPTH,
) -> tuple[Comment | None, bool]:
    """Parse a single comment dict.

    Returns (comment_or_None, truncated_flag).
    - `comment_or_None` is None if the raw payload is missing 'id'.
    - `truncated_flag` is True if any reply at any depth in this subtree
      was dropped because the recursion reached `max_depth`. The parent
      Comment at the cap level is preserved with `replies=[]` (D-17),
      not discarded — losing the parent would be silent data loss.
    """
    cid = raw.get("id")
    if cid is None:
        return None, False

    user_obj = raw.get("user")
    user = user_obj.get("name", "") if isinstance(user_obj, dict) else ""

    truncated = False
    if depth >= max_depth:
        replies: list[Comment] = []
        # If the raw payload had any replies, mark truncation so the
        # caller can emit a single warning at the top of the subtree
        # (D-18 — quiet enough to not spam, loud enough to notice).
        if raw.get("replies"):
            truncated = True
    else:
        replies_raw = raw.get("replies") or []
        replies = []
        for r in replies_raw:
            if not isinstance(r, dict):
                continue
            child, child_trunc = _parse_one(r, depth + 1, max_depth=max_depth)
            if child is not None:
                replies.append(child)
            if child_trunc:
                truncated = True

    return (
        Comment(
            comment_id=str(cid),
            user=user,
            body=raw.get("body") or "",
            created_at=raw.get("createdAt") or "",
            paragraph_id=raw.get("paragraphId"),
            replies=replies,
        ),
        truncated,
    )


def parse_comments_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    raw_comments = raw.get("comments") or []
    parsed: list[Comment] = []
    for r in raw_comments:
        if not isinstance(r, dict):
            continue
        comment, was_truncated = _parse_one(r)
        if comment is None:
            continue
        parsed.append(comment)
        if was_truncated:
            # D-18: one warning per truncated top-level subtree, naming
            # the comment id and the depth cap. Loud enough to notice in
            # the log; quiet enough to avoid one-per-dropped-reply spam.
            logger.warning(
                "comment %s truncated: replies beyond depth %d dropped",
                comment.comment_id,
                _MAX_COMMENT_DEPTH,
            )
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
