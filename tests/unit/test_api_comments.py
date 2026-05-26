import json
import logging
from pathlib import Path

from local_story_archive.api import comments as comments_mod
from local_story_archive.api.comments import parse_comments_page


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


def test_parse_comments_page_accepts_v4_comment_shape():
    raw = {
        "comments": [
            {
                "id": "1493815966##1777570246#134769c15e",
                "author": {"name": "tri72027"},
                "body": "Lần 3, gét gô",
                "createDate": "2026-04-30T17:30:46Z",
                "paragraphId": None,
                "voteCount": 5,
            },
            {
                "id": "1493815966#para#1776348404#e22792c88c",
                "author": {"name": "Thinreong6"},
                "body": "Nhảy cái bộp liền",
                "createDate": "2026-04-16T14:06:44Z",
                "paragraphId": "para",
            },
        ],
        "nextUrl": "https://api.wattpad.com/v4/parts/1493815966/comments?offsetId=next",
    }

    parsed, next_url = parse_comments_page(raw)

    assert [c.user for c in parsed] == ["tri72027", "Thinreong6"]
    assert parsed[0].created_at == "2026-04-30T17:30:46Z"
    assert parsed[0].paragraph_id is None
    assert parsed[0].like_count == 5
    assert parsed[1].paragraph_id == "para"
    assert next_url == "https://api.wattpad.com/v4/parts/1493815966/comments?offsetId=next"


def test_parse_comments_page_accepts_v5_sentiment_likes():
    raw = {
        "comments": [
            {
                "commentId": {
                    "namespace": "comments",
                    "resourceId": "1346950910_ed07664520f26bb94b3cc8497e6812f8_1754215612_87f70d2ae0",
                },
                "created": "2025-08-03T10:06:52Z",
                "replyCount": 1,
                "resource": {
                    "namespace": "paragraphs",
                    "resourceId": "1346950910_ed07664520f26bb94b3cc8497e6812f8",
                },
                "sentiments": {":like:": {"count": 2}},
                "text": "??c h?t v? l?i l?i chap1",
                "user": {"name": "caubemattich"},
            },
            {
                "commentId": {
                    "namespace": "comments",
                    "resourceId": "1346950910_ed07664520f26bb94b3cc8497e6812f8_1726682766_8a666de929",
                },
                "created": "2024-09-18T18:06:06Z",
                "resource": {
                    "namespace": "paragraphs",
                    "resourceId": "1346950910_ed07664520f26bb94b3cc8497e6812f8",
                },
                "sentiments": {},
                "text": "Ch?a bi?t hay kh?ng nh?ng t?c gi? c? b? h?p gu n?n nh?y lu?n",
                "user": {"name": "Meisuria"},
            },
        ]
    }

    parsed, next_url = parse_comments_page(raw)

    assert next_url is None
    assert parsed[0].comment_id == "1346950910_ed07664520f26bb94b3cc8497e6812f8_1754215612_87f70d2ae0"
    assert parsed[0].user == "caubemattich"
    assert parsed[0].body == "??c h?t v? l?i l?i chap1"
    assert parsed[0].created_at == "2025-08-03T10:06:52Z"
    assert parsed[0].paragraph_id == "ed07664520f26bb94b3cc8497e6812f8"
    assert parsed[0].like_count == 2
    assert parsed[1].like_count == 0

def test_parse_comments_page_accepts_v5_part_resource_comments():
    raw = {
        "comments": [
            {
                "commentId": {
                    "namespace": "comments",
                    "resourceId": "1346952590_1778432283_2a62aea0e2",
                },
                "created": "2026-05-10T16:58:03Z",
                "resource": {"namespace": "parts", "resourceId": "1346952590"},
                "sentiments": {":like:": {"count": 1}},
                "text": "end comment",
                "user": {"name": "Huuyyeenn1"},
            }
        ],
        "pagination": {
            "after": {
                "namespace": "comments",
                "resourceId": "1346952590_1778432283_2a62aea0e2",
            }
        },
    }

    parsed, next_url = parse_comments_page(raw)

    assert next_url is None
    assert parsed[0].comment_id == "1346952590_1778432283_2a62aea0e2"
    assert parsed[0].paragraph_id is None
    assert parsed[0].body == "end comment"
    assert parsed[0].like_count == 1


def test_parse_comments_page_prefers_sentiment_like_count():
    raw = {
        "comments": [
            {
                "id": "c1",
                "author": {"name": "bob"},
                "body": "hello",
                "createDate": "2026-04-30T17:30:46Z",
                "sentiments": {":like:": {"count": 3}},
                "voteCount": 1,
            },
        ]
    }

    parsed, _ = parse_comments_page(raw)

    assert parsed[0].like_count == 3


class _FakeCommentsResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeCommentsClient:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url: str):
        self.urls.append(url)
        for marker, payload in self.responses:
            if marker in url:
                return _FakeCommentsResponse(payload)
        raise AssertionError(f"unexpected URL: {url}")


def test_fetch_inline_comments_uses_v5_part_endpoint_with_likes():
    v5_page = {
        "comments": [
            {
                "commentId": {
                    "namespace": "comments",
                    "resourceId": "1346950910_ed07664520f26bb94b3cc8497e6812f8_1754215612_87f70d2ae0",
                },
                "created": "2025-08-03T10:06:52Z",
                "resource": {
                    "namespace": "paragraphs",
                    "resourceId": "1346950910_ed07664520f26bb94b3cc8497e6812f8",
                },
                "sentiments": {":like:": {"count": 2}},
                "text": "first",
                "user": {"name": "caubemattich"},
            }
        ]
    }
    client = _FakeCommentsClient(
        [
            ("/v5/comments/namespaces/parts/resources/1346950910/comments", v5_page),
        ]
    )

    comments = comments_mod.fetch_inline_comments(client, "1346950910")

    assert len(comments) == 1
    assert comments[0].like_count == 2
    assert any(
        "/v5/comments/namespaces/parts/resources/1346950910/comments" in url
        for url in client.urls
    )

def test_fetch_end_comments_uses_v5_part_endpoint_and_after_pagination():
    first_page = {
        "comments": [
            {
                "commentId": {"resourceId": "1346952590_1778432283_2a62aea0e2"},
                "created": "2026-05-10T16:58:03Z",
                "resource": {"namespace": "parts", "resourceId": "1346952590"},
                "text": "first end comment",
                "user": {"name": "one"},
                "replyCount": 1,
            },
            {
                "commentId": {
                    "resourceId": "1346952590_abca6a598fc6944ab1b70d5b69d07e6e_1778431930_47b17f9aca"
                },
                "created": "2026-05-10T16:52:10Z",
                "resource": {
                    "namespace": "paragraphs",
                    "resourceId": "1346952590_abca6a598fc6944ab1b70d5b69d07e6e",
                },
                "text": "inline comment",
                "user": {"name": "inline"},
            },
        ],
        "pagination": {
            "after": {
                "namespace": "comments",
                "resourceId": "1346952590_1778432283_2a62aea0e2",
            }
        },
    }
    second_page = {
        "comments": [
            {
                "commentId": {"resourceId": "1346952590_1778430000_deadbeef"},
                "created": "2026-05-10T16:20:00Z",
                "resource": {"namespace": "parts", "resourceId": "1346952590"},
                "text": "second end comment",
                "user": {"name": "two"},
            }
        ]
    }
    replies_page = {
        "comments": [
            {
                "commentId": {"resourceId": "1346952590_1778432283_replyhash"},
                "created": "2026-05-10T17:00:00Z",
                "resource": {
                    "namespace": "comments",
                    "resourceId": "1346952590_1778432283_2a62aea0e2",
                },
                "text": "reply to first end comment",
                "user": {"name": "reply-user"},
            }
        ]
    }

    class SequentialClient:
        def __init__(self):
            self.urls = []
            self.pages = [first_page, second_page]

        def get(self, url: str):
            self.urls.append(url)
            if "/v5/comments/namespaces/comments/resources/" in url:
                return _FakeCommentsResponse(replies_page)
            return _FakeCommentsResponse(self.pages.pop(0))

    client = SequentialClient()

    comments = comments_mod.fetch_end_comments(client, "1346952590")

    assert [comment.body for comment in comments] == [
        "first end comment",
        "inline comment",
        "second end comment",
    ]
    assert [reply.body for reply in comments[0].replies] == ["reply to first end comment"]
    assert "/v5/comments/namespaces/parts/resources/1346952590/comments" in client.urls[0]
    assert any(
        "/v5/comments/namespaces/comments/resources/1346952590_1778432283_2a62aea0e2/comments"
        in url
        for url in client.urls
    )
    assert any("after=1346952590%231778432283%232a62aea0e2" in url for url in client.urls)

def test_parse_comments_page_accepts_nested_vote_count_shape():
    raw = {
        "comments": [
            {
                "id": "c1",
                "author": {"name": "bob"},
                "body": "hello",
                "createDate": "2026-04-30T17:30:46Z",
                "paragraphId": None,
                "votes": {"count": 7},
            },
        ],
        "nextUrl": None,
    }

    parsed, _ = parse_comments_page(raw)

    assert parsed[0].like_count == 7


def test_parse_comments_page_nests_flat_reply_rows():
    raw = {
        "comments": [
            {
                "id": "parent",
                "author": {"name": "parent-user"},
                "body": "parent body",
                "createDate": "2026-04-16T14:06:44Z",
                "paragraphId": "para",
            },
            {
                "id": "reply",
                "author": {"name": "reply-user"},
                "body": "reply body",
                "createDate": "2026-04-16T14:07:44Z",
                "paragraphId": None,
                "parentId": "parent",
            },
        ],
        "nextUrl": None,
    }

    parsed, next_url = parse_comments_page(raw)

    assert next_url is None
    assert len(parsed) == 1
    assert parsed[0].comment_id == "parent"
    assert parsed[0].paragraph_id == "para"
    assert parsed[0].replies[0].comment_id == "reply"
    assert parsed[0].replies[0].user == "reply-user"

def test_parse_comments_page_accepts_latest_replies_shape():
    raw = {
        "comments": [
            {
                "id": "parent",
                "author": {"name": "parent-user"},
                "body": "parent body",
                "createDate": "2026-04-16T14:06:44Z",
                "paragraphId": "para",
                "latestReplies": [
                    {
                        "id": "reply",
                        "author": {"name": "reply-user"},
                        "body": "reply body",
                        "createDate": "2026-04-16T14:07:44Z",
                        "paragraphId": None,
                        "parentId": "parent",
                    }
                ],
            },
        ],
        "nextUrl": None,
    }

    parsed, next_url = parse_comments_page(raw)

    assert next_url is None
    assert len(parsed) == 1
    assert parsed[0].comment_id == "parent"
    assert parsed[0].replies[0].comment_id == "reply"


def test_fetch_inline_and_end_use_v5_part_comments_and_filter_by_resource():
    class FakeResponse:
        def json(self):
            return {
                "comments": [
                    {
                        "commentId": {"resourceId": "1493815966_1777570246_134769c15e"},
                        "user": {"name": "end-user"},
                        "text": "end",
                        "created": "2026-04-30T17:30:46Z",
                        "resource": {"namespace": "parts", "resourceId": "1493815966"},
                    },
                    {
                        "commentId": {"resourceId": "1493815966_para_1776348404_e22792c88c"},
                        "user": {"name": "inline-user"},
                        "text": "inline",
                        "created": "2026-04-16T14:06:44Z",
                        "resource": {"namespace": "paragraphs", "resourceId": "1493815966_para"},
                    },
                ],
            }

    class FakeClient:
        def __init__(self):
            self.urls: list[str] = []

        def get(self, url: str):
            self.urls.append(url)
            return FakeResponse()

    inline_client = FakeClient()
    end_client = FakeClient()

    inline = comments_mod.fetch_inline_comments(inline_client, "1493815966")
    end = comments_mod.fetch_end_comments(end_client, "1493815966")

    assert inline_client.urls == [
        "https://www.wattpad.com/v5/comments/namespaces/parts/"
        "resources/1493815966/comments?limit=100"
    ]
    assert end_client.urls == [
        "https://www.wattpad.com/v5/comments/namespaces/parts/"
        "resources/1493815966/comments?limit=100"
    ]
    assert [c.comment_id for c in inline] == ["1493815966_para_1776348404_e22792c88c"]
    assert [c.comment_id for c in end] == [
        "1493815966_1777570246_134769c15e",
        "1493815966_para_1776348404_e22792c88c",
    ]

def test_fetch_inline_comments_fetches_declared_reply_pages():
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.urls: list[str] = []

        def get(self, url: str):
            self.urls.append(url)
            if "/v5/comments/namespaces/comments/resources/parent/comments" in url:
                return FakeResponse({
                    "comments": [
                        {
                            "commentId": {"resourceId": "reply"},
                            "user": {"name": "reply-user"},
                            "text": "reply body",
                            "created": "2026-04-16T14:07:44Z",
                            "resource": {"namespace": "comments", "resourceId": "parent"},
                        }
                    ],
                })
            return FakeResponse({
                "comments": [
                    {
                        "commentId": {"resourceId": "parent"},
                        "user": {"name": "parent-user"},
                        "text": "parent body",
                        "created": "2026-04-16T14:06:44Z",
                        "resource": {"namespace": "paragraphs", "resourceId": "1493815966_para"},
                        "replyCount": 1,
                    }
                ],
            })

    comments = comments_mod.fetch_inline_comments(FakeClient(), "1493815966")

    assert len(comments) == 1
    assert comments[0].comment_id == "parent"
    assert [reply.comment_id for reply in comments[0].replies] == ["reply"]

def test_fetch_inline_comments_fetches_v5_declared_reply_pages():
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.urls: list[str] = []

        def get(self, url: str):
            self.urls.append(url)
            if "/v5/comments/namespaces/comments/resources/" in url:
                return FakeResponse({
                    "comments": [
                        {
                            "commentId": {"resourceId": "1346952590_parent_reply"},
                            "created": "2026-05-10T16:07:44Z",
                            "resource": {"namespace": "comments", "resourceId": "1346952590_parent"},
                            "text": "reply body",
                            "user": {"name": "reply-user"},
                        }
                    ]
                })
            return FakeResponse({
                "comments": [
                    {
                        "commentId": {"resourceId": "1346952590_parent"},
                        "created": "2026-05-10T16:06:44Z",
                        "resource": {"namespace": "paragraphs", "resourceId": "1346952590_para"},
                        "replyCount": 1,
                        "text": "parent body",
                        "user": {"name": "parent-user"},
                    }
                ]
            })

    client = FakeClient()

    comments = comments_mod.fetch_inline_comments(client, "1346952590")

    assert len(comments) == 1
    assert [reply.body for reply in comments[0].replies] == ["reply body"]
    assert client.urls[1] == (
        "https://www.wattpad.com/v5/comments/namespaces/comments/"
        "resources/1346952590_parent/comments?limit=100"
    )

def test_fetch_inline_comments_uses_underscore_comment_id_for_replies():
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.urls: list[str] = []

        def get(self, url: str):
            self.urls.append(url)
            if "/replies" in url:
                return FakeResponse({"replies": [], "nextUrl": None})
            return FakeResponse({
                "comments": [
                    {
                        "id": "part#paragraph#timestamp#hash",
                        "author": {"name": "parent-user"},
                        "body": "parent body",
                        "createDate": "2026-04-16T14:06:44Z",
                        "paragraphId": "para",
                        "numReplies": 1,
                    }
                ],
                "nextUrl": None,
            })

    client = FakeClient()

    comments_mod.fetch_inline_comments(client, "1493815966")

    assert client.urls[1] == (
        "https://www.wattpad.com/v4/comments/"
        "part_paragraph_timestamp_hash/replies?limit=100"
    )


# --- Phase 1 REL-01 recursion-cap tests ---


def _nest(level: int) -> dict:
    """Build a comment dict with `level` levels of nested replies.

    level=0 produces a leaf comment (no replies).
    level=N produces a comment whose replies contain one nested comment of level N-1.
    """
    if level == 0:
        return {"id": "c0", "body": "leaf", "user": {"name": "u"}}
    return {
        "id": f"c{level}",
        "body": f"level {level}",
        "user": {"name": "u"},
        "replies": [_nest(level - 1)],
    }


def test_parse_one_caps_recursion_at_default_max_depth():
    from local_story_archive.api.comments import _MAX_COMMENT_DEPTH, _parse_one

    raw = _nest(15)
    comment, truncated = _parse_one(raw)
    assert comment is not None
    assert truncated is True
    assert _MAX_COMMENT_DEPTH == 10  # REL-01 default

    # Walk down 10 levels — each level should have exactly one reply.
    cursor = comment
    for _ in range(10):
        assert cursor is not None
        assert len(cursor.replies) == 1
        cursor = cursor.replies[0]
    # The 10th-level comment is preserved (D-17) but its replies are empty.
    assert cursor is not None
    assert cursor.replies == []


def test_parse_one_respects_custom_max_depth():
    from local_story_archive.api.comments import _parse_one

    raw = _nest(10)
    comment, truncated = _parse_one(raw, max_depth=3)
    assert comment is not None
    assert truncated is True

    cursor = comment
    for _ in range(3):
        assert len(cursor.replies) == 1
        cursor = cursor.replies[0]
    assert cursor.replies == []


def test_parse_one_no_truncation_when_depth_below_cap():
    from local_story_archive.api.comments import _parse_one

    raw = _nest(5)
    comment, truncated = _parse_one(raw, max_depth=10)
    assert comment is not None
    assert truncated is False

    cursor = comment
    for _ in range(5):
        assert len(cursor.replies) == 1
        cursor = cursor.replies[0]
    assert cursor.replies == []  # natural leaf, not truncation


def test_parse_one_no_recursion_error_on_30_level_chain():
    """A 30-level chain must not raise RecursionError even at the default cap of 10."""
    from local_story_archive.api.comments import _parse_one

    raw = _nest(30)
    comment, truncated = _parse_one(raw)
    assert comment is not None
    assert truncated is True


def test_parse_one_returns_none_when_id_missing():
    from local_story_archive.api.comments import _parse_one

    comment, truncated = _parse_one({"body": "no id", "user": {"name": "u"}})
    assert comment is None
    assert truncated is False


def test_parse_one_skips_non_dict_replies():
    """Defensive: malformed reply entries (strings, None) must not crash."""
    from local_story_archive.api.comments import _parse_one

    raw = {
        "id": "c1",
        "user": {"name": "u"},
        "body": "x",
        "replies": ["not-a-dict", None, {"id": "c2", "user": {"name": "v"}, "body": "y"}],
    }
    comment, truncated = _parse_one(raw)
    assert comment is not None
    assert truncated is False
    assert len(comment.replies) == 1
    assert comment.replies[0].comment_id == "c2"


def test_parse_comments_page_logs_warning_on_truncation(caplog):
    raw = {"comments": [_nest(15)], "nextUrl": None}
    with caplog.at_level(logging.WARNING, logger="local_story_archive.api.comments"):
        parsed, next_url = parse_comments_page(raw)
    assert len(parsed) == 1
    assert next_url is None
    # Exactly one warning record from this logger.
    records = [r for r in caplog.records if r.name == "local_story_archive.api.comments"]
    assert len(records) == 1
    msg = records[0].getMessage().lower()
    assert "truncat" in msg
    # Message names the parent comment id (c15 — the top of the chain).
    assert "c15" in records[0].getMessage()


def test_parse_comments_page_no_warning_when_under_cap(caplog):
    raw = {"comments": [_nest(5)], "nextUrl": None}
    with caplog.at_level(logging.WARNING, logger="local_story_archive.api.comments"):
        parsed, _ = parse_comments_page(raw)
    assert len(parsed) == 1
    records = [r for r in caplog.records if r.name == "local_story_archive.api.comments"]
    assert records == []


def test_parse_comments_page_emits_one_warning_per_truncated_top_level(caplog):
    """Two top-level comments, only one of which truncates — exactly one warning."""
    raw = {
        "comments": [_nest(15), _nest(5)],  # first truncates, second does not
        "nextUrl": None,
    }
    with caplog.at_level(logging.WARNING, logger="local_story_archive.api.comments"):
        parsed, _ = parse_comments_page(raw)
    assert len(parsed) == 2
    records = [r for r in caplog.records if r.name == "local_story_archive.api.comments"]
    assert len(records) == 1
    assert "c15" in records[0].getMessage()


def test_parse_one_monkeypatch_constant_changes_behavior(monkeypatch):
    """D-11: tests can monkeypatch _MAX_COMMENT_DEPTH; new calls pick up new default."""
    from local_story_archive.api import comments as comments_mod

    monkeypatch.setattr(comments_mod, "_MAX_COMMENT_DEPTH", 2)
    # _parse_one's default for max_depth was bound at function-def time
    # to the original constant (10). To honor the patched value tests
    # must pass it explicitly. This test documents that contract.
    raw = _nest(5)
    # Default still 10 (function default captured at definition).
    _, t_default = comments_mod._parse_one(raw)
    assert t_default is False
    # Explicit pass uses the patched value.
    _, t_explicit = comments_mod._parse_one(raw, max_depth=2)
    assert t_explicit is True
