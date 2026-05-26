from pathlib import Path

from local_story_archive.archive.repository import ArchiveRepository
from local_story_archive.models import Comment, Part, Story
from local_story_archive.scrape.chapter_html import ChapterContent


def test_repository_initializes_versioned_schema(output_dir: Path):
    repo = ArchiveRepository(output_dir).connect()

    version = repo.db.execute("SELECT version FROM schema_meta").fetchone()["version"]
    table_names = {
        row["name"]
        for row in repo.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }

    assert version == 1
    assert {
        "schema_meta",
        "stories",
        "story_tags",
        "parts",
        "paragraphs",
        "comments",
        "runs",
        "artifacts",
    } <= table_names
    repo.close()


def test_repository_round_trips_story_parts_paragraphs_and_comments(output_dir: Path):
    repo = ArchiveRepository(output_dir).connect()
    story = Story(
        story_id="42",
        title="Hi There",
        author_username="bob",
        description="desc",
        cover_url="https://x/c.jpg",
        tags=["tag-a", "tag-b"],
        parts=[Part(part_id="100", ordinal=1, title="One", url="https://w/100")],
        votes=7,
        reads=8,
        completed=True,
    )
    content = ChapterContent(
        text="First\n\nSecond",
        paragraphs=[
            {"id": "p1", "text": "First", "html": "<b>First</b>"},
            {"id": "p2", "text": "Second", "html": "Second"},
        ],
        images=[],
    )
    inline = [
        Comment(
            comment_id="c1",
            user="alice",
            body="Nice",
            created_at="now",
            paragraph_id="p1",
            replies=[Comment(comment_id="r1", user="bob", body="Thanks", created_at="now")],
        )
    ]
    end = [Comment(comment_id="c2", user="carol", body="End", created_at="later")]

    with repo.transaction():
        repo.upsert_story(story)
        repo.upsert_part(story.story_id, story.parts[0], content, "<html>raw</html>", inline, end)

    saved_story = repo.get_story("42")
    saved_parts = repo.list_parts("42")
    saved_paragraphs = repo.list_paragraphs("100")
    inline_by_paragraph = repo.comments_by_paragraph("100")
    end_comments = repo.end_comments("100")

    assert saved_story is not None
    assert saved_story["title"] == "Hi There"
    assert saved_story["tags"] == ["tag-a", "tag-b"]
    assert saved_parts[0]["body_text"] == "First\n\nSecond"
    assert saved_paragraphs[0]["html"] == "<b>First</b>"
    assert inline_by_paragraph["p1"][0]["body"] == "Nice"
    assert inline_by_paragraph["p1"][0]["replies"][0]["body"] == "Thanks"
    assert end_comments[0]["body"] == "End"
    repo.close()


def test_repository_lists_library_entries_sorted(output_dir: Path):
    repo = ArchiveRepository(output_dir).connect()
    first = Story(story_id="2", title="Beta", author_username="zoe")
    second = Story(story_id="1", title="Alpha", author_username="amy")

    with repo.transaction():
        repo.upsert_story(first)
        repo.upsert_story(second)

    entries = repo.list_library_entries()

    assert [(entry.author, entry.title, entry.dir_name) for entry in entries] == [
        ("amy", "Alpha", "1_alpha"),
        ("zoe", "Beta", "2_beta"),
    ]
    repo.close()
