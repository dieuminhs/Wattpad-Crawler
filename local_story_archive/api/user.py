"""User-scoped Wattpad endpoints: library, reading lists, and list contents.

Note: Wattpad does not expose a separate 'favorites' endpoint. A user's
favorited stories are part of their library (added via the heart button) and
optionally organized into a named reading list called e.g. 'Favorites'.
Fetch them via fetch_library() or via fetch_reading_lists() + fetch_list_story_ids().
"""
from typing import Any
from urllib.parse import quote

from local_story_archive.client import RateLimitedClient

LIBRARY_URL = "https://www.wattpad.com/api/v3/users/{username}/library?limit=200"
READING_LISTS_URL = "https://www.wattpad.com/api/v3/users/{username}/lists"
LIST_STORIES_URL = "https://www.wattpad.com/api/v3/lists/{list_id}/stories?limit=500"
CURRENT_USER_URL = "https://www.wattpad.com/api/v3/users/me"

# Hard cap on pagination loops to prevent runaway requests if the server
# misbehaves and returns the same nextUrl forever.
_MAX_PAGES = 200


def parse_library(raw: dict[str, Any]) -> list[str]:
    stories = raw.get("stories") or []
    return [str(s["id"]) for s in stories if isinstance(s, dict) and s.get("id") is not None]


def parse_reading_lists(raw: dict[str, Any]) -> list[dict[str, Any]]:
    lists = raw.get("lists") or []
    return [
        {
            "id": str(L["id"]),
            "name": L.get("name", ""),
            "num_stories": L.get("numStories", 0),
        }
        for L in lists
        if isinstance(L, dict) and L.get("id") is not None
    ]


def parse_list_stories(raw: dict[str, Any]) -> list[str]:
    stories = raw.get("stories") or []
    return [str(s["id"]) for s in stories if isinstance(s, dict) and s.get("id") is not None]

def parse_current_username(raw: dict[str, Any]) -> str:
    username = raw.get("username") or raw.get("name")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("Wattpad current-user response did not include a username")
    return username.strip()


def _paginate(client: RateLimitedClient, start_url: str) -> list[dict[str, Any]]:
    """Walk the nextUrl/nextPage chain, with cycle and max-page guards."""
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    url = start_url
    while url and url not in seen and len(pages) < _MAX_PAGES:
        seen.add(url)
        data = client.get(url).json()
        pages.append(data)
        url = data.get("nextUrl") or data.get("nextPage") or ""
    return pages


def fetch_library(client: RateLimitedClient, username: str) -> list[str]:
    start = LIBRARY_URL.format(username=quote(username, safe=""))
    ids: list[str] = []
    for page in _paginate(client, start):
        ids.extend(parse_library(page))
    return ids

def fetch_current_username(client: RateLimitedClient) -> str:
    return parse_current_username(client.get(CURRENT_USER_URL).json())


def fetch_reading_lists(
    client: RateLimitedClient, username: str
) -> list[dict[str, Any]]:
    # Reading lists are capped well below 100 per user; single page is sufficient.
    url = READING_LISTS_URL.format(username=quote(username, safe=""))
    return parse_reading_lists(client.get(url).json())


def fetch_list_story_ids(client: RateLimitedClient, list_id: str) -> list[str]:
    start = LIST_STORIES_URL.format(list_id=quote(list_id, safe=""))
    ids: list[str] = []
    for page in _paginate(client, start):
        ids.extend(parse_list_stories(page))
    return ids
