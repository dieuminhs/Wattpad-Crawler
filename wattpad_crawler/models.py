from dataclasses import dataclass, field
from typing import Literal

PartStatus = Literal[
    "pending", "in_progress", "done", "failed", "body_text_failed", "gone", "private"
]
StoryStatus = Literal[
    "pending", "in_progress", "done", "failed", "gone", "private"
]


@dataclass
class Part:
    part_id: str
    ordinal: int
    title: str
    url: str
    last_modified: str | None = None


@dataclass
class Story:
    story_id: str
    title: str
    author_username: str
    description: str = ""
    cover_url: str = ""
    tags: list[str] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    last_modified: str | None = None
    votes: int = 0
    reads: int = 0
    completed: bool = False


@dataclass
class Comment:
    comment_id: str
    user: str
    body: str
    created_at: str
    paragraph_id: str | None = None     # set for inline comments
    like_count: int = 0
    replies: list["Comment"] = field(default_factory=list)
