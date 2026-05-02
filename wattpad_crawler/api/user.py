from typing import Any

from wattpad_crawler.client import RateLimitedClient

LIBRARY_URL = "https://www.wattpad.com/api/v3/users/{username}/library?limit=200"
READING_LISTS_URL = "https://www.wattpad.com/api/v3/users/{username}/lists"
LIST_STORIES_URL = "https://www.wattpad.com/api/v3/lists/{list_id}/stories?limit=500"


def parse_library(raw: dict[str, Any]) -> list[str]:
    stories = raw.get("stories") or []
    return [str(s["id"]) for s in stories if s.get("id") is not None]


def parse_reading_lists(raw: dict[str, Any]) -> list[dict[str, Any]]:
    lists = raw.get("lists") or []
    return [
        {
            "id": str(L["id"]),
            "name": L.get("name", ""),
            "num_stories": L.get("numStories", 0),
        }
        for L in lists
        if L.get("id") is not None
    ]


def fetch_library(client: RateLimitedClient, username: str) -> list[str]:
    ids: list[str] = []
    url = LIBRARY_URL.format(username=username)
    while url:
        data = client.get(url).json()
        ids.extend(parse_library(data))
        url = data.get("nextUrl") or data.get("nextPage") or ""
    return ids


def fetch_reading_lists(
    client: RateLimitedClient, username: str
) -> list[dict[str, Any]]:
    return parse_reading_lists(
        client.get(READING_LISTS_URL.format(username=username)).json()
    )


def fetch_list_story_ids(client: RateLimitedClient, list_id: str) -> list[str]:
    ids: list[str] = []
    url = LIST_STORIES_URL.format(list_id=list_id)
    while url:
        data = client.get(url).json()
        ids.extend(str(s["id"]) for s in data.get("stories", []) if s.get("id") is not None)
        url = data.get("nextUrl") or ""
    return ids
