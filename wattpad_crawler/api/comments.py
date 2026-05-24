import logging
from typing import Any
from urllib.parse import quote

from wattpad_crawler.auth import AuthFailedError
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.models import Comment

logger = logging.getLogger(__name__)

PART_COMMENTS_URL = "https://www.wattpad.com/v4/parts/{part_id}/comments?limit=100"
COMMENT_REPLIES_URL = "https://www.wattpad.com/v4/comments/{comment_id}/replies?limit=100"
PART_COMMENTS_V5_URL = (
    "https://www.wattpad.com/v5/comments/namespaces/parts/"
    "resources/{resource_id}/comments?limit=100"
)
PARAGRAPH_COMMENTS_URL = (
    "https://www.wattpad.com/v5/comments/namespaces/paragraphs/"
    "resources/{resource_id}/comments?"
)
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
    cid = _comment_id(raw)
    if cid is None:
        return None, False

    user_obj = raw.get("user") or raw.get("author")
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
        replies_raw = raw.get("replies") or raw.get("latestReplies") or []
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
            body=raw.get("body") or raw.get("text") or "",
            created_at=raw.get("createdAt") or raw.get("createDate") or raw.get("created") or "",
            paragraph_id=_paragraph_id(raw),
            like_count=_like_count(raw),
            replies=replies,
        ),
        truncated,
    )


def _comment_id(raw: dict[str, Any]) -> str | None:
    comment_id = raw.get("id")
    if comment_id is None:
        comment_id_obj = raw.get("commentId")
        if isinstance(comment_id_obj, dict):
            comment_id = comment_id_obj.get("resourceId")
    return str(comment_id) if comment_id is not None else None


def _paragraph_id(raw: dict[str, Any]) -> str | None:
    paragraph_id = raw.get("paragraphId")
    if paragraph_id is None:
        resource = raw.get("resource")
        if isinstance(resource, dict) and resource.get("namespace") == "paragraphs":
            resource_id = resource.get("resourceId")
            if isinstance(resource_id, str):
                parts = resource_id.split("_", 1)
                paragraph_id = parts[1] if len(parts) == 2 else resource_id
    return str(paragraph_id) if paragraph_id is not None else None

def _like_count(raw: dict[str, Any]) -> int:
    count = None
    sentiments = raw.get("sentiments")
    if isinstance(sentiments, dict):
        like_sentiment = sentiments.get(":like:")
        if isinstance(like_sentiment, dict):
            count = like_sentiment.get("count") or like_sentiment.get("total")

    if count is None:
        for key in (
            "voteCount",
            "votesCount",
            "likeCount",
            "likesCount",
            "numLikes",
            "numVotes",
            "likes",
            "votes",
        ):
            value = raw.get(key)
            if isinstance(value, dict):
                value = value.get("count") or value.get("total")
            if value is not None:
                count = value
                break
    try:
        return int(count or 0)
    except (TypeError, ValueError):
        return 0

def parse_comments_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    raw_comments = raw.get("comments") or []
    parsed: list[Comment] = []
    parent_by_id: dict[str, str | None] = {}
    for r in raw_comments:
        if not isinstance(r, dict):
            continue
        comment, was_truncated = _parse_one(r)
        if comment is None:
            continue
        parsed.append(comment)
        parent_by_id[comment.comment_id] = _parent_comment_id(r)
        if was_truncated:
            # D-18: one warning per truncated top-level subtree, naming
            # the comment id and the depth cap. Loud enough to notice in
            # the log; quiet enough to avoid one-per-dropped-reply spam.
            logger.warning(
                "comment %s truncated: replies beyond depth %d dropped",
                comment.comment_id,
                _MAX_COMMENT_DEPTH,
            )
    return _nest_flat_replies(parsed, parent_by_id), raw.get("nextUrl")

def _pagination_after(raw: dict[str, Any]) -> str | None:
    pagination = raw.get("pagination")
    if not isinstance(pagination, dict):
        return None
    after = pagination.get("after")
    if not isinstance(after, dict):
        return None
    resource_id = after.get("resourceId")
    return str(resource_id) if resource_id is not None else None

def _v5_next_url(url: str, raw: dict[str, Any]) -> str | None:
    after = _pagination_after(raw)
    if after is None:
        return None
    base_url = url.split("?", 1)[0]
    return f"{base_url}?limit=100&after={quote(after.replace('_', '#'), safe='')}"

def parse_replies_page(raw: dict[str, Any]) -> tuple[list[Comment], str | None]:
    raw_replies = raw.get("replies") or []
    parsed: list[Comment] = []
    for r in raw_replies:
        if not isinstance(r, dict):
            continue
        comment, was_truncated = _parse_one(r)
        if comment is None:
            continue
        parsed.append(comment)
        if was_truncated:
            logger.warning(
                "comment %s truncated: replies beyond depth %d dropped",
                comment.comment_id,
                _MAX_COMMENT_DEPTH,
            )
    return parsed, raw.get("nextUrl")


def _parent_comment_id(raw: dict[str, Any]) -> str | None:
    parent_id = (
        raw.get("parentId")
        or raw.get("parent_id")
        or raw.get("parentCommentId")
        or raw.get("parent_comment_id")
    )
    return str(parent_id) if parent_id is not None else None

def _reply_count(raw: dict[str, Any]) -> int:
    count = raw.get("replyCount")
    if count is None:
        count = raw.get("reply_count")
    if count is None:
        count = raw.get("numReplies")
    try:
        return int(count or 0)
    except (TypeError, ValueError):
        return 0


def _nest_flat_replies(
    comments: list[Comment],
    parent_by_id: dict[str, str | None],
) -> list[Comment]:
    by_id = {comment.comment_id: comment for comment in comments}
    roots: list[Comment] = []
    for comment in comments:
        parent_id = parent_by_id.get(comment.comment_id)
        parent = by_id.get(parent_id or "")
        if parent is None:
            roots.append(comment)
            continue
        if comment not in parent.replies:
            parent.replies.append(comment)
    return roots


def _fetch_all(
    client: RateLimitedClient,
    url: str,
    *,
    fetch_declared_replies: bool = True,
) -> list[Comment]:
    out: list[Comment] = []
    seen: set[str] = set()
    pages = 0
    while url and url not in seen and pages < _MAX_PAGES:
        seen.add(url)
        pages += 1
        data = client.get(url).json()
        comments, next_url = parse_comments_page(data)
        if fetch_declared_replies:
            _fetch_declared_replies(client, comments, data)
        out.extend(comments)
        url = next_url or _v5_next_url(url, data) or ""
    return out

def _fetch_declared_replies(
    client: RateLimitedClient,
    comments: list[Comment],
    raw_page: dict[str, Any],
) -> None:
    raw_by_id = {
        str(raw["id"]): raw
        for raw in raw_page.get("comments") or []
        if isinstance(raw, dict) and raw.get("id") is not None
    }
    for comment in comments:
        raw = raw_by_id.get(comment.comment_id)
        if raw is None or comment.replies or _reply_count(raw) <= 0:
            continue
        replies = _fetch_reply_pages(
            client,
            COMMENT_REPLIES_URL.format(comment_id=_url_comment_id(comment.comment_id)),
        )
        comment.replies.extend(replies)

def _fetch_reply_pages(client: RateLimitedClient, url: str) -> list[Comment]:
    out: list[Comment] = []
    seen: set[str] = set()
    pages = 0
    while url and url not in seen and pages < _MAX_PAGES:
        seen.add(url)
        pages += 1
        data = client.get(url).json()
        replies, next_url = parse_replies_page(data)
        out.extend(replies)
        url = next_url or ""
    return out

def _url_comment_id(comment_id: str) -> str:
    return quote(comment_id.replace("#", "_"), safe="")



def _normalized_comment_id(comment_id: str) -> str:
    return comment_id.replace("#", "_")


def _merge_like_counts_from_v5(v4_comments: list[Comment], v5_comments: list[Comment]) -> None:
    likes_by_id = {
        _normalized_comment_id(comment.comment_id): comment.like_count
        for comment in v5_comments
        if comment.like_count > 0
    }

    def update(comment: Comment) -> None:
        like_count = likes_by_id.get(_normalized_comment_id(comment.comment_id))
        if like_count is not None:
            comment.like_count = like_count
        for reply in comment.replies:
            update(reply)

    for comment in v4_comments:
        update(comment)


def fetch_paragraph_comments(
    client: RateLimitedClient,
    part_id: str,
    paragraph_id: str,
) -> list[Comment]:
    resource_id = f"{part_id}_{paragraph_id}"
    comments = _fetch_all(
        client,
        PARAGRAPH_COMMENTS_URL.format(resource_id=quote(resource_id, safe="")),
        fetch_declared_replies=False,
    )
    return [comment for comment in comments if comment.paragraph_id is not None]


def _refresh_inline_like_counts(
    client: RateLimitedClient,
    part_id: str,
    comments: list[Comment],
) -> None:
    paragraph_ids = sorted({comment.paragraph_id for comment in comments if comment.paragraph_id})
    for paragraph_id in paragraph_ids:
        try:
            v5_comments = fetch_paragraph_comments(client, part_id, paragraph_id)
        except AuthFailedError:
            raise
        except Exception as exc:
            logger.warning(
                "v5 paragraph comment likes failed for part %s paragraph %s: %s",
                part_id,
                paragraph_id,
                exc,
            )
            continue
        _merge_like_counts_from_v5(comments, v5_comments)

def fetch_inline_comments(client: RateLimitedClient, part_id: str) -> list[Comment]:
    comments = _fetch_all(client, PART_COMMENTS_URL.format(part_id=quote(part_id, safe="")))
    inline_comments = [comment for comment in comments if comment.paragraph_id is not None]
    _refresh_inline_like_counts(client, part_id, inline_comments)
    return inline_comments


def fetch_end_comments(client: RateLimitedClient, part_id: str) -> list[Comment]:
    comments = _fetch_all(
        client,
        PART_COMMENTS_V5_URL.format(resource_id=quote(part_id, safe="")),
        fetch_declared_replies=False,
    )
    return [comment for comment in comments if comment.paragraph_id is None]
