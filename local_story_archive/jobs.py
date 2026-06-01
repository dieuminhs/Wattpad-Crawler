import hashlib
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import count
from typing import Literal
from urllib.parse import urlsplit

from local_story_archive.api import comments as api_comments
from local_story_archive.api import story as api_story
from local_story_archive.archive import store
from local_story_archive.archive.compact import compact_archive
from local_story_archive.archive.repository import ArchiveRepository
from local_story_archive.archive.state import Manifest
from local_story_archive.auth import AuthFailedError
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import Config
from local_story_archive.models import Part, Story
from local_story_archive.render import epub as render_epub
from local_story_archive.render import html as render_html
from local_story_archive.render import txt as render_txt
from local_story_archive.scrape.chapter_html import ChapterContent, extract_chapter

logger = logging.getLogger(__name__)

_HTML_BODY_CLOSE_RE = re.compile(r"</body>\s*</html>\s*$", re.IGNORECASE)


def _path_size(path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0

def _part_content_exists(
    output_dir,
    repo: ArchiveRepository,
    story: Story,
    part: Part,
) -> bool:
    row = repo.db.execute(
        "SELECT status, body_text, raw_html FROM parts WHERE story_id = ? AND part_id = ?",
        (story.story_id, part.part_id),
    ).fetchone()
    if row and row["status"] == "done" and (row["body_text"] or row["raw_html"]):
        return True
    if row and row["status"] == "done":
        paragraph = repo.db.execute(
            "SELECT 1 FROM paragraphs WHERE part_id = ? LIMIT 1",
            (part.part_id,),
        ).fetchone()
        if paragraph is not None:
            return True
    parts_dir = store.story_dir(output_dir, story) / "parts"
    if not parts_dir.exists():
        return False
    prefix = f"{part.ordinal:02d}_{part.part_id}_"
    return any(path.is_file() and path.stat().st_size > 0 for path in parts_dir.glob(f"{prefix}*.txt"))


def _part_id_from_url(url: str) -> str:
    path = urlsplit(url).path
    match = re.match(r"^/(\d+)", path)
    if not match:
        raise ValueError(f"cannot determine Wattpad part id from URL: {url}")
    return match.group(1)


def fetch_full_chapter_html(client: RateLimitedClient, url: str) -> str:
    """Fetch initial chapter HTML plus any extra storytext pages."""
    part_id = _part_id_from_url(url)
    first_page = client.get(url).text
    extra_pages: list[str] = []
    ajax_headers = {
        "Accept": "text/html, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": url,
    }

    for page in count(start=2):
        response = client.get(
            "https://www.wattpad.com/apiv2/",
            params={"m": "storytext", "id": part_id, "page": page},
            headers=ajax_headers,
        )
        text = response.text
        if not text.strip():
            break
        extra_pages.append(text)

    if not extra_pages:
        return first_page

    extra_html = "\n".join(extra_pages)
    match = _HTML_BODY_CLOSE_RE.search(first_page)
    if not match:
        return "\n".join([first_page, extra_html])
    return first_page[: match.start()] + "\n" + extra_html + first_page[match.start() :]


@dataclass
class JobDeps:
    """Indirection layer so tests can inject fakes."""

    fetch_story: Callable
    fetch_chapter_html: Callable
    parse_chapter: Callable
    fetch_inline_comments: Callable
    fetch_end_comments: Callable
    fetch_cover_bytes: Callable


@dataclass
class PartArchiveResult:
    part: Part
    raw_html: str
    content: ChapterContent
    inline_comments: list
    end_comments: list
    body_hash: str


def _default_deps() -> JobDeps:
    def fetch_cover_bytes(client: RateLimitedClient, url: str) -> bytes:
        if not url:
            return b""
        try:
            return client.get(url).content
        except Exception as e:
            logger.warning("cover fetch failed: %s", e)
            return b""

    return JobDeps(
        fetch_story=api_story.fetch_story,
        fetch_chapter_html=fetch_full_chapter_html,
        parse_chapter=extract_chapter,
        fetch_inline_comments=api_comments.fetch_inline_comments,
        fetch_end_comments=api_comments.fetch_end_comments,
        fetch_cover_bytes=fetch_cover_bytes,
    )


ProgressCallback = Callable[[str, dict], None]


def _noop_progress(_kind: str, _data: dict) -> None:
    pass


def _fetch_comments_or_empty(
    fetch_comments: Callable,
    client: RateLimitedClient,
    part_id: str,
    comment_kind: str,
    emit: ProgressCallback,
) -> list:
    try:
        return fetch_comments(client, part_id)
    except AuthFailedError:
        raise
    except Exception as e:
        logger.warning("%s comments failed for part %s: %s", comment_kind, part_id, e)
        emit(
            "comments.failed",
            {
                "part_id": part_id,
                "kind": comment_kind,
                "error": str(e),
            },
        )
        return []


def _fetch_part_payload(
    deps: JobDeps,
    client: RateLimitedClient,
    part,
    emit: ProgressCallback,
    *,
    archive_comments: bool,
) -> PartArchiveResult:
    raw_html = deps.fetch_chapter_html(client, part.url)
    content: ChapterContent = deps.parse_chapter(raw_html)
    inline = []
    end = []
    if archive_comments:
        inline = _fetch_comments_or_empty(
            deps.fetch_inline_comments,
            client,
            part.part_id,
            "inline",
            emit,
        )
        end = _fetch_comments_or_empty(
            deps.fetch_end_comments,
            client,
            part.part_id,
            "end",
            emit,
        )
    body_hash = hashlib.sha256(content.text.encode("utf-8")).hexdigest()
    return PartArchiveResult(part, raw_html, content, inline, end, body_hash)


def archive_story(
    cfg: Config,
    client: RateLimitedClient,
    manifest: Manifest,
    story_id: str,
    *,
    deps: JobDeps | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    deps = deps or _default_deps()
    emit = progress or _noop_progress
    started_at = time.monotonic()
    archive_db_path = cfg.output_dir / "archive.sqlite"
    archive_db_size_before = _path_size(archive_db_path)
    logger.info("Archiving story %s", story_id)
    emit("story.fetch", {"story_id": story_id})
    story: Story = deps.fetch_story(client, story_id)
    emit(
        "story.start",
        {
            "story_id": story.story_id,
            "title": story.title,
            "author": story.author_username,
            "parts_total": len(story.parts),
        },
    )

    manifest.upsert_story(story)
    manifest.upsert_parts(story)
    repo = ArchiveRepository(cfg.output_dir).connect()
    with repo.transaction():
        repo.upsert_story(story)
    store.write_story_metadata(cfg.output_dir, story)
    if story.cover_url:
        try:
            cover = deps.fetch_cover_bytes(client, story.cover_url)
            if cover:
                store.write_cover(cfg.output_dir, story, cover)
        except Exception as e:
            logger.warning("cover fetch failed for %s: %s", story.story_id, e)

    pending_parts = []
    for part in story.parts:
        existing = manifest.get_part(story.story_id, part.part_id)
        if existing and existing["status"] == "done" and _part_content_exists(cfg.output_dir, repo, story, part):
            emit("part.skipped", {"part_id": part.part_id, "ordinal": part.ordinal})
            continue
        emit(
            "part.start",
            {
                "part_id": part.part_id,
                "ordinal": part.ordinal,
                "title": part.title,
            },
        )
        manifest.set_part_status(story.story_id, part.part_id, "in_progress")
        pending_parts.append(part)

    def persist_result(result: PartArchiveResult) -> None:
        part = result.part
        store.write_part_files(
            cfg.output_dir,
            story,
            part,
            result.content,
            result.raw_html,
            result.inline_comments,
            result.end_comments,
        )
        with repo.transaction():
            repo.upsert_part(
                story.story_id,
                part,
                result.content,
                result.raw_html,
                result.inline_comments,
                result.end_comments,
                body_hash=result.body_hash,
            )
        manifest.set_part_status(
            story.story_id,
            part.part_id,
            "done",
            body_hash=result.body_hash,
        )
        emit(
            "part.done",
            {
                "part_id": part.part_id,
                "ordinal": part.ordinal,
                "inline_comments": len(result.inline_comments),
                "end_comments": len(result.end_comments),
            },
        )

    if pending_parts:
        with ThreadPoolExecutor(max_workers=cfg.workers_per_story) as executor:
            future_to_part = {
                executor.submit(
                    _fetch_part_payload,
                    deps,
                    client,
                    part,
                    emit,
                    archive_comments=cfg.archive_comments,
                ): part
                for part in pending_parts
            }
            for future in as_completed(future_to_part):
                part = future_to_part[future]
                try:
                    persist_result(future.result())
                except AuthFailedError as e:
                    manifest.set_part_status(
                        story.story_id,
                        part.part_id,
                        "failed",
                        last_error=str(e),
                    )
                    emit("auth.failed", {
                        "part_id": part.part_id,
                        "status_code": e.status_code,
                        "url": e.url,
                        "message": str(e),
                    })
                    for pending in future_to_part:
                        pending.cancel()
                    raise
                except Exception as e:
                    logger.exception("part %s failed: %s", part.part_id, e)
                    manifest.set_part_status(
                        story.story_id,
                        part.part_id,
                        "failed",
                        last_error=str(e),
                    )
                    emit("part.failed", {"part_id": part.part_id, "error": str(e)})

    sd = store.story_dir(cfg.output_dir, story)
    emit("render.start", {"story_id": story.story_id})

    # REL-04 / D-15: run all three renderers unconditionally, each in its
    # own try/except; collect per-format ok/failed status. story.done
    # carries the breakdown so SSE consumers see exactly which formats
    # succeeded. After the loop, raise RenderError IFF all three failed
    # — partial success keeps the job alive so existing artifacts ship.
    render_status: dict[str, Literal["ok", "failed"]] = {}
    for name, fn in (
        ("txt", render_txt.render_txt),
        ("html", render_html.render_html),
        ("epub", render_epub.render_epub),
    ):
        try:
            if name in {"html", "epub"}:
                fn(sd, cfg.export_preset)
            else:
                fn(sd)
            render_status[name] = "ok"
        except Exception as e:
            logger.exception("render(%s) failed for %s: %s", name, story.story_id, e)
            emit("render.failed", {"format": name, "error": str(e)})
            render_status[name] = "failed"

    emit(
        "story.done",
        {
            "story_id": story.story_id,
            "render_status": render_status,
            "duration_seconds": round(time.monotonic() - started_at, 2),
            "archive_db_bytes_before": archive_db_size_before,
            "archive_db_bytes_after": _path_size(archive_db_path),
        },
    )

    if all(v == "failed" for v in render_status.values()):
        raise RenderError(f"all renders failed: {render_status}")

    if cfg.compact_after_archive:
        compact_result = compact_archive(cfg.output_dir, dry_run=False)
        emit(
            "archive.compacted",
            {
                "story_id": story.story_id,
                  "files_removed": compact_result.files_removed,
                  "bytes_removed": compact_result.bytes_removed,
                  "db_bytes_removed": compact_result.db_bytes_removed,
                  "archive_db_bytes_after": _path_size(archive_db_path),
              },
          )

    logger.info(
        "Archived story %s in %.2fs; parts=%s pending=%s; archive.sqlite %s -> %s bytes",
        story.story_id,
        time.monotonic() - started_at,
        len(story.parts),
        len(pending_parts),
        archive_db_size_before,
        _path_size(archive_db_path),
    )

    repo.close()


def refresh_story_comments(
    cfg: Config,
    client: RateLimitedClient,
    story_id: str,
    *,
    deps: JobDeps | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    deps = deps or _default_deps()
    emit = progress or _noop_progress
    logger.info("Refreshing comments for story %s", story_id)
    emit("comments.refresh.start", {"story_id": story_id})
    story: Story = deps.fetch_story(client, story_id)
    store.write_story_metadata(cfg.output_dir, story)
    repo = ArchiveRepository(cfg.output_dir).connect()
    try:
        with repo.transaction():
            repo.upsert_story(story)
        for part in story.parts:
            emit(
                "comments.refresh.part",
                {"part_id": part.part_id, "ordinal": part.ordinal, "title": part.title},
            )
            inline = _fetch_comments_or_empty(
                deps.fetch_inline_comments,
                client,
                part.part_id,
                "inline",
                emit,
            )
            end = _fetch_comments_or_empty(
                deps.fetch_end_comments,
                client,
                part.part_id,
                "end",
                emit,
            )
            store.write_comment_files(cfg.output_dir, story, part, inline, end)
            with repo.transaction():
                repo.replace_comments(part.part_id, inline, end)
            emit(
                "comments.refresh.done",
                {
                    "part_id": part.part_id,
                    "ordinal": part.ordinal,
                    "inline_comments": len(inline),
                    "end_comments": len(end),
                },
            )
        emit("comments.refresh.story_done", {"story_id": story.story_id})
    finally:
        repo.close()


class ResolveError(Exception):
    pass


class RenderError(Exception):
    """All renderers (TXT, HTML, EPUB) failed for one story.

    Raised by archive_story() after the render loop completes when every
    format in render_status is "failed". JobRunner._run catches this as
    a normal Exception and routes to set_failed(str(e)); archive_many
    records it in the per-story results dict via the same path. Partial
    render failures (>=1 format succeeded) do NOT raise — the story.done
    event carries the per-format breakdown instead.
    """

    pass


_STORY_URL_RE = re.compile(r"wattpad\.com/story/(\d+)")
_PART_URL_RE = re.compile(r"wattpad\.com/(\d+)(?:[/?#-]|$)")
_NUMERIC_RE = re.compile(r"^\d+$")


def resolve_story_id(target: str) -> str:
    """Resolve a CLI input to a numeric story_id.

    Accepts a bare numeric id or /story/<id> URLs.
    """
    target = target.strip()
    if _NUMERIC_RE.match(target):
        return target
    m = _STORY_URL_RE.search(target)
    if m:
        return m.group(1)
    raise ResolveError(
        f"Cannot resolve {target!r} to a story ID. "
        "Pass a numeric story ID or a Wattpad /story/<id> URL."
    )


def resolve_url_story_id(client: RateLimitedClient, target: str) -> str:
    """Resolve a URL command target to a story_id, including chapter URLs."""
    target = target.strip()
    try:
        return resolve_story_id(target)
    except ResolveError:
        pass

    m = _PART_URL_RE.search(target)
    if m:
        return api_story.fetch_part_story_id(client, m.group(1))

    raise ResolveError(
        f"Cannot resolve {target!r} to a story ID. "
        "Pass a numeric story ID, a Wattpad /story/<id> URL, or a Wattpad chapter URL."
    )


def archive_many(
    cfg: Config,
    client: RateLimitedClient,
    manifest: Manifest,
    story_ids: list[str],
    *,
    deps: JobDeps | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, str]:
    """Archive a list of stories sequentially. Returns {story_id: status}."""
    emit = progress or _noop_progress
    results: dict[str, str] = {}
    emit("batch.start", {"total": len(story_ids), "story_ids": list(story_ids)})
    for i, sid in enumerate(story_ids):
        emit("batch.story", {"index": i, "total": len(story_ids), "story_id": sid})
        try:
            archive_story(cfg, client, manifest, sid, deps=deps, progress=progress)
            results[sid] = "done"
        except Exception as e:
            # NOTE (Phase 2 / D-18): if e is AuthFailedError from a mid-batch cookie
            # expiry, we currently treat it as a single-story failure and continue.
            # The next story's first request will also 401, so the batch fails loudly
            # in N steps rather than 1. Acceptable for v1; a future improvement could
            # re-raise AuthFailedError here to abort the batch immediately.
            logger.exception("story %s failed: %s", sid, e)
            results[sid] = f"failed: {e}"
            emit("batch.failed", {"story_id": sid, "error": str(e)})
    emit("batch.done", {"results": results})
    return results
