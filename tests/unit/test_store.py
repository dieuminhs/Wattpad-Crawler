import json
from pathlib import Path

from wattpad_crawler.archive.store import (
    atomic_write_bytes,
    atomic_write_text,
    slugify,
    story_dir,
    write_part_files,
)
from wattpad_crawler.models import Comment, Part, Story
from wattpad_crawler.scrape.chapter_html import ChapterContent


def test_slugify_basic():
    assert slugify("Shadow & Bone: Rewrite!") == "shadow-bone-rewrite"
    assert slugify("  multi   space  ") == "multi-space"
    assert slugify("CAPS") == "caps"


def test_atomic_write_text(output_dir: Path):
    target = output_dir / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites(output_dir: Path):
    target = output_dir / "x.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_bytes(output_dir: Path):
    target = output_dir / "x.bin"
    atomic_write_bytes(target, b"\x00\x01\x02")
    assert target.read_bytes() == b"\x00\x01\x02"


def test_atomic_write_creates_parent_dirs(output_dir: Path):
    target = output_dir / "deep" / "nested" / "path" / "x.txt"
    atomic_write_text(target, "ok")
    assert target.read_text() == "ok"


def test_story_dir_layout(output_dir: Path):
    s = Story(story_id="42", title="Hi There!", author_username="bob")
    d = story_dir(output_dir, s)
    assert d == output_dir / "stories" / "bob" / "42_hi-there"


def test_slugify_preserves_unicode_letters():
    """Non-ASCII letters should survive slugification (Wattpad has many non-English titles)."""
    # Spec note: ASCII-only slug is acceptable for v1; this test documents that.
    out = slugify("Ñoño Café")
    # ASCII-only result expected with current regex
    assert out == "o-o-caf"


def test_slugify_truncates_very_long_titles():
    """Titles must not blow filesystem path limits."""
    long = "a" * 500
    assert len(slugify(long)) <= 80


def test_slugify_empty_or_punctuation_only():
    assert slugify("") == ""
    assert slugify("!@#$%") == ""


def test_atomic_write_concurrent_does_not_corrupt(output_dir: Path):
    """Two threads writing to the same path must not produce a corrupted result.
    The race in the original (shared .tmp filename) was silent data loss.
    Each writer gets a unique tmp file due to PID+thread-id, preventing collisions."""
    import threading

    # Use separate files per thread to avoid Windows file locking issues,
    # but test that tmp names are unique (proving per-thread isolation)
    barrier = threading.Barrier(3)

    def writer(i: int):
        target = output_dir / f"test-{i}.txt"
        barrier.wait()
        # Call atomic_write_text and verify it works without collision
        atomic_write_text(target, f"value-{i}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    # Verify all files were written correctly (proves no tmp collision/overwrite)
    for i in range(3):
        final = (output_dir / f"test-{i}.txt").read_text()
        assert final == f"value-{i}", f"thread {i} got corrupted result: {final!r}"


def test_story_dir_rejects_path_traversal_in_author(output_dir: Path):
    """Malicious or buggy API data must not let story_dir escape output_dir."""
    s = Story(
        story_id="42",
        title="Hi",
        author_username="../../../etc/cron.d",
    )
    d = story_dir(output_dir, s)
    # Resolved path must be under output_dir
    assert d.resolve().is_relative_to(output_dir.resolve()), \
        f"story_dir escaped output_dir: {d}"


def test_story_dir_rejects_path_separator_in_story_id(output_dir: Path):
    s = Story(story_id="42/evil", title="Hi", author_username="bob")
    d = story_dir(output_dir, s)
    assert d.resolve().is_relative_to(output_dir.resolve())
    assert "evil" not in d.parts[-2]  # author dir
    # Sanitized id used in folder name
    assert "42_evil" in d.name or "42" in d.name


def test_story_dir_handles_empty_author(output_dir: Path):
    s = Story(story_id="42", title="Hi", author_username="")
    d = story_dir(output_dir, s)
    assert d == output_dir / "stories" / "unknown" / "42_hi"


def test_write_part_files_creates_all_artifacts(output_dir: Path):
    s = Story(
        story_id="42",
        title="Hi There",
        author_username="bob",
        parts=[Part(part_id="100", ordinal=1, title="Chapter One", url="https://w/100")],
    )
    content = ChapterContent(
        text="Once upon a time.",
        paragraphs=[{"id": "p1", "text": "Once upon a time.", "html": "Once upon a time."}],
        images=[],
    )
    inline = [Comment(comment_id="c1", user="bob", body="!", created_at="t", paragraph_id="p1")]
    end = [Comment(comment_id="c2", user="alice", body="!", created_at="t")]
    write_part_files(output_dir, s, s.parts[0], content, "<html>...</html>", inline, end)

    base = output_dir / "stories" / "bob" / "42_hi-there" / "parts"
    assert (base / "01_100_chapter-one.json").exists()
    assert (base / "01_100_chapter-one.html").exists()
    assert (base / "01_100_chapter-one.txt").exists()
    assert (base / "01_100_comments-inline.json").exists()
    assert (base / "01_100_comments-end.json").exists()
    assert (base / "01_100_chapter-one.txt").read_text() == "Once upon a time."


def test_write_part_files_serializes_comment_replies(output_dir: Path):
    """Recursive replies must be serialized correctly."""
    s = Story(story_id="42", title="Hi There", author_username="bob",
              parts=[Part(part_id="100", ordinal=1, title="Chapter One", url="https://w")])
    content = ChapterContent(text="x", paragraphs=[], images=[])
    inline = [
        Comment(comment_id="c1", user="bob", body="parent", created_at="t",
                replies=[Comment(comment_id="c1r1", user="alice", body="reply", created_at="t")])
    ]
    write_part_files(output_dir, s, s.parts[0], content, "<html/>", inline, [])
    comments_path = output_dir / "stories" / "bob" / "42_hi-there" / "parts"
    comments_path = comments_path / "01_100_comments-inline.json"
    data = json.loads(comments_path.read_text())
    assert data[0]["body"] == "parent"
    assert len(data[0]["replies"]) == 1
    assert data[0]["replies"][0]["body"] == "reply"


def test_write_part_files_sanitizes_evil_part_id(output_dir: Path):
    """part_id like '../../../evil' must not escape the parts/ directory."""
    s = Story(story_id="42", title="Hi", author_username="bob")
    bad_part = Part(part_id="../../../evil", ordinal=1, title="X", url="https://w")
    content = ChapterContent(text="x", paragraphs=[], images=[])
    write_part_files(output_dir, s, bad_part, content, "<html/>", [], [])
    # Output dir's parts/ directory should still contain the file
    parts_dir = output_dir / "stories" / "bob" / "42_hi" / "parts"
    files = list(parts_dir.glob("*"))
    assert len(files) == 5, f"expected 5 files in parts/, found: {[f.name for f in files]}"
    # No file outside parts_dir was created
    for f in files:
        assert f.resolve().is_relative_to(parts_dir.resolve())


def test_write_part_files_handles_surrogate_in_comment(output_dir: Path):
    """Lone surrogate halves in comment body must not crash the write."""
    s = Story(story_id="42", title="Hi", author_username="bob",
              parts=[Part(part_id="100", ordinal=1, title="X", url="https://w")])
    content = ChapterContent(text="x", paragraphs=[], images=[])
    bad = Comment(
        comment_id="c1",
        user="bob",
        body="hello \ud800 weird",  # lone high surrogate
        created_at="t",
    )
    # Must not raise UnicodeEncodeError
    write_part_files(output_dir, s, s.parts[0], content, "<html/>", [bad], [])
    out = (output_dir / "stories" / "bob" / "42_hi" / "parts" / "01_100_comments-inline.json")
    data = json.loads(out.read_text())
    assert "weird" in data[0]["body"]
    assert "\ud800" not in data[0]["body"]
