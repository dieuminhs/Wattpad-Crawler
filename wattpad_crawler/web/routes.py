import dataclasses
import json
import os
import shutil
import threading
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sse_starlette.sse import EventSourceResponse

from wattpad_crawler.api.user import fetch_library, fetch_list_story_ids
from wattpad_crawler.archive import store
from wattpad_crawler.archive.repository import ArchiveRepository
from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.auth import AuthError, validate_cookie
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.jobs import (
    ResolveError,
    archive_many,
    archive_story,
    refresh_story_comments,
    resolve_url_story_id,
)
from wattpad_crawler.web.library_browser import scan_library

router = APIRouter()
_LIBRARY_PAGE_SIZE = 25
_LIBRARY_FILTERS = {"all", "bookmarked", "has_cover", "no_cover"}


def _search_text(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _entry_matches_query(entry, query: str) -> bool:
    haystack = " ".join(
        [
            entry.story_id,
            entry.title,
            entry.author,
            entry.description,
            " ".join(entry.tags),
        ]
    )
    return _search_text(query) in _search_text(haystack)


def _entry_matches_filter(entry, filter_name: str) -> bool:
    if filter_name == "bookmarked":
        return entry.bookmarked
    if filter_name == "has_cover":
        return entry.has_cover
    if filter_name == "no_cover":
        return not entry.has_cover
    return True


def _library_page_url(page: int, query: str, filter_name: str) -> str:
    params = []
    if query:
        params.append(("q", query))
    if filter_name != "all":
        params.append(("filter", filter_name))
    params.append(("page", str(page)))
    if not params:
        return "/library"
    return "/library?" + urlencode(params)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _save_cookie(output_dir: Path, cookie: str) -> None:
    """Write/update the cookie line in _config.toml atomically.

    AUTH-05 / D-19: Process-kill safe — an interrupt during the write leaves
    either the old file or no change, never a half-written one. Mirrors
    archive/store.py:atomic_write_text + _tmp_path. Same-directory tmp file
    guarantees same-volume rename on Windows. PID/TID suffix avoids collision
    if two writers ever race on the same target (last writer wins on rename;
    neither corrupts the target).
    """
    config_path = output_dir / "_config.toml"
    cookie = cookie.strip()
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        new_lines = []
        replaced = False
        for line in lines:
            if line.lstrip().startswith("cookie "):
                new_lines.append(f"cookie = {_toml_string(cookie)}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"cookie = {_toml_string(cookie)}")
        new_text = "\n".join(new_lines) + "\n"
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        new_text = (
            f"cookie = {_toml_string(cookie)}\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n"
        )
    # Atomic write: same-directory tmp + os.replace. PID/TID suffix avoids
    # collision if two writers race on the same target. Cleanup on exception
    # so we don't leave stale tmp files in a long-running web process.
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    tmp = config_path.with_suffix(config_path.suffix + suffix)
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, config_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_write_config(output_dir: Path, text: str) -> None:
    config_path = output_dir / "_config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    tmp = config_path.with_suffix(config_path.suffix + suffix)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, config_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _save_runtime_config(
    output_dir: Path,
    *,
    cookie: str,
    rate_limit_per_sec: float,
    workers_per_story: int,
) -> None:
    text = (
        f"cookie = {_toml_string(cookie)}\n"
        f"rate_limit_per_sec = {rate_limit_per_sec}\n"
        f"workers_per_story = {workers_per_story}\n"
    )
    _atomic_write_config(output_dir, text)


def _mask(s: str) -> str:
    if not s:
        return ""
    return s[:4] + "…" + s[-4:] if len(s) > 8 else "…"


@router.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "current_cookie_masked": _mask(cfg.cookie),
            "output_dir": str(cfg.output_dir),
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/setup", response_model=None)
def setup_post(
    request: Request,
    cookie: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    cfg = request.app.state.cfg
    templates = request.app.state.templates
    submitted = cookie.strip()

    # D-12: validate BEFORE saving. Build a transient Config + client around
    # the submitted cookie; do not mutate request.app.state.cfg yet.
    transient_cfg = dataclasses.replace(cfg, cookie=submitted)
    error_kind: str | None = None
    error_message: str = ""
    try:
        with RateLimitedClient(transient_cfg) as transient_client:
            validate_cookie(transient_client)
    except AuthError as e:
        error_kind = "auth"
        error_message = str(e)
    except httpx.RequestError as e:
        error_kind = "network"
        error_message = f"Could not reach Wattpad: {e}"
    except Exception as e:
        error_kind = "unexpected"
        error_message = f"Validation failed: {e!r}"

    if error_kind is not None:
        # D-09: re-render the form with status 400; D-11: show masked attempted
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "current_cookie_masked": _mask(cfg.cookie),
                "attempted_cookie_masked": _mask(submitted),
                "error_kind": error_kind,
                "error_message": error_message,
                "output_dir": str(cfg.output_dir),
                "saved": False,
            },
            status_code=400,
        )

    # Validation succeeded — persist atomically (D-19) and reload config.
    _save_cookie(cfg.output_dir, submitted)
    from wattpad_crawler.config import load_config
    request.app.state.cfg = load_config(cfg.output_dir)
    return RedirectResponse(url="/setup?saved=1", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    mgr = request.app.state.job_manager
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "has_cookie": bool(cfg.cookie),
            "recent_jobs": mgr.list_jobs()[:10],
        },
    )


def _build_work(cfg, kind: str, args: dict):
    """Build a JobWork callable that opens its own client+manifest, runs the job,
    then closes them."""

    def work(emit):
        client = RateLimitedClient(cfg)
        manifest = Manifest(cfg.output_dir).connect()
        try:
            if kind == "story":
                archive_story(cfg, client, manifest, args["story_id"], progress=emit)
            elif kind == "library":
                ids = fetch_library(client, args["username"])
                archive_many(cfg, client, manifest, ids, progress=emit)
            elif kind == "list":
                ids = fetch_list_story_ids(client, args["list_id"])
                archive_many(cfg, client, manifest, ids, progress=emit)
            elif kind == "comments":
                refresh_story_comments(cfg, client, args["story_id"], progress=emit)
            elif kind == "comments_many":
                for story_id in args["story_ids"]:
                    refresh_story_comments(cfg, client, story_id, progress=emit)
        finally:
            manifest.close()
            client.close()

    return work


@router.post("/jobs")
async def submit_job(request: Request) -> RedirectResponse:
    form = await request.form()
    kind = form.get("kind")
    cfg = request.app.state.cfg
    mgr = request.app.state.job_manager
    runner = request.app.state.job_runner

    if kind == "story":
        target = form.get("target", "").strip()
        try:
            with RateLimitedClient(cfg) as client:
                sid = resolve_url_story_id(client, target)
        except ResolveError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        job = mgr.create("archive_story", {"story_id": sid, "target": target})
        runner.submit(job, _build_work(cfg, "story", {"story_id": sid}))
    elif kind == "library":
        username = form.get("username", "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="username required")
        job = mgr.create("archive_library", {"username": username})
        runner.submit(job, _build_work(cfg, "library", {"username": username}))
    elif kind == "list":
        list_id = form.get("list_id", "").strip()
        if not list_id:
            raise HTTPException(status_code=400, detail="list_id required")
        job = mgr.create("archive_list", {"list_id": list_id})
        runner.submit(job, _build_work(cfg, "list", {"list_id": list_id}))
    elif kind == "comments":
        story_id = form.get("story_id", "").strip()
        if not story_id:
            raise HTTPException(status_code=400, detail="story_id required")
        job = mgr.create("refresh_comments", {"story_id": story_id})
        runner.submit(job, _build_work(cfg, "comments", {"story_id": story_id}))
    else:
        raise HTTPException(status_code=400, detail=f"unknown kind: {kind}")

    return RedirectResponse(url=f"/?job_id={job.job_id}", status_code=303)


@router.post("/library/bulk")
async def library_bulk(request: Request) -> RedirectResponse:
    form = await request.form()
    action = str(form.get("bulk_action") or "")
    story_ids = [str(value).strip() for value in form.getlist("story_ids") if str(value).strip()]
    if not story_ids:
        raise HTTPException(status_code=400, detail="select at least one story")

    cfg = request.app.state.cfg
    if action == "refresh_comments":
        job = request.app.state.job_manager.create("refresh_comments", {"story_ids": story_ids})
        request.app.state.job_runner.submit(
            job,
            _build_work(cfg, "comments_many", {"story_ids": story_ids}),
        )
        return RedirectResponse(url=f"/?job_id={job.job_id}", status_code=303)

    if action == "remove":
        removed = sum(1 for story_id in story_ids if _remove_story_archive(cfg, story_id))
        return RedirectResponse(url=f"/library?removed={removed}", status_code=303)

    raise HTTPException(status_code=400, detail=f"unknown bulk action: {action}")


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> HTMLResponse:
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="job.html",
        context={"job": job},
    )


@router.get("/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: str, after_seq: int = 0):
    """Server-Sent Events stream of job progress.

    D-09: query parameter is `after_seq` (the highest seq the client has
    already consumed). D-10: if events between after_seq and the oldest
    surviving seq have been evicted from the deque (REL-02 cap), emit a
    synthetic `events.evicted` event ahead of the snapshot so the UI
    knows older events were dropped to save memory.
    """
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        import asyncio
        import time as _time

        last_seq = after_seq
        # Per-stream eviction-warning latch (RESEARCH Open Question #2 — RESOLVED).
        # Reconnection creates a fresh event_gen and re-evaluates the gap by
        # design; within one SSE connection we announce at most once.
        gap_announced = False

        while True:
            if await request.is_disconnected():
                break

            # On first poll only, check whether the client's cursor has
            # been evicted from the deque. If so, emit a synthetic
            # events.evicted event ahead of the regular snapshot.
            if not gap_announced:
                oldest = job.oldest_seq()
                if oldest and last_seq + 1 < oldest:
                    dropped = oldest - 1 - last_seq
                    yield {
                        "data": json.dumps(
                            {
                                "kind": "events.evicted",
                                "data": {
                                    "dropped_count": dropped,
                                    "requested_after_seq": after_seq,
                                    "oldest_available_seq": oldest,
                                },
                                "ts": _time.time(),
                            }
                        )
                    }
                gap_announced = True

            new_events = job.snapshot_events(last_seq)
            for ev in new_events:
                last_seq = ev.seq
                yield {
                    "data": json.dumps(
                        {
                            "kind": ev.kind,
                            "data": ev.data,
                            "seq": ev.seq,
                            "ts": ev.timestamp,
                        }
                    )
                }

            if job.status.value in ("done", "failed"):
                yield {
                    "data": json.dumps(
                        {
                            "kind": "__status__",
                            "data": {"status": job.status.value, "error": job.error},
                        }
                    )
                }
                return
            # 250ms polling — fine for personal-use UI; threading.Event-to-asyncio
            # bridge is fiddly and not worth the complexity for this scope.
            await asyncio.sleep(0.25)

    return EventSourceResponse(event_gen())


@router.get("/library", response_class=HTMLResponse)
def library(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    all_entries = scan_library(cfg.output_dir)
    query = request.query_params.get("q", "").strip()
    filter_name = request.query_params.get("filter", "all")
    if filter_name not in _LIBRARY_FILTERS:
        filter_name = "all"
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1

    filtered_entries = [
        entry
        for entry in all_entries
        if _entry_matches_filter(entry, filter_name)
        and (not query or _entry_matches_query(entry, query))
    ]
    total_entries = len(all_entries)
    total_filtered = len(filtered_entries)
    total_pages = max(1, (total_filtered + _LIBRARY_PAGE_SIZE - 1) // _LIBRARY_PAGE_SIZE)
    page = min(page, total_pages)
    page_start = (page - 1) * _LIBRARY_PAGE_SIZE
    entries = filtered_entries[page_start : page_start + _LIBRARY_PAGE_SIZE]
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "entries": entries,
            "total_entries": total_entries,
            "total_filtered": total_filtered,
            "query": query,
            "filter_name": filter_name,
            "page": page,
            "total_pages": total_pages,
            "prev_page_url": _library_page_url(page - 1, query, filter_name) if page > 1 else "",
            "next_page_url": (
                _library_page_url(page + 1, query, filter_name) if page < total_pages else ""
            ),
            "filter_options": [
                ("all", "All", _library_page_url(1, query, "all")),
                ("bookmarked", "Bookmarked", _library_page_url(1, query, "bookmarked")),
                ("has_cover", "Has cover", _library_page_url(1, query, "has_cover")),
                ("no_cover", "No cover", _library_page_url(1, query, "no_cover")),
            ],
            "reset_story_id": request.query_params.get("reset"),
            "removed_story_id": request.query_params.get("removed"),
            "bookmarked_story_id": request.query_params.get("bookmarked"),
        },
    )


@router.get("/jobs/{job_id}/summary")
def job_summary(request: Request, job_id: str) -> JSONResponse:
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(
        {
            "job_id": job.job_id,
            "kind": job.kind,
            "args": job.args,
            "status": job.status.value,
            "error": job.error,
            "next_seq": job.next_seq,
            "events": [
                {"kind": ev.kind, "data": ev.data, "seq": ev.seq}
                for ev in job.snapshot_events(0)
            ],
        }
    )


@router.get("/config", response_class=HTMLResponse)
def config_get(request: Request) -> HTMLResponse:
    cfg = request.app.state.cfg
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "cfg": cfg,
            "saved": request.query_params.get("saved") == "1",
            "error_message": "",
        },
    )


@router.post("/config", response_model=None)
def config_post(
    request: Request,
    rate_limit_per_sec: str = Form(...),
    workers_per_story: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    cfg = request.app.state.cfg
    try:
        rate = float(rate_limit_per_sec)
        workers = int(workers_per_story)
        if rate <= 0:
            raise ValueError("Requests per second must be greater than 0.")
        if workers < 1:
            raise ValueError("Chapter workers must be at least 1.")
    except ValueError as e:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="config.html",
            context={"cfg": cfg, "saved": False, "error_message": str(e)},
            status_code=400,
        )

    _save_runtime_config(
        cfg.output_dir,
        cookie=cfg.cookie,
        rate_limit_per_sec=rate,
        workers_per_story=workers,
    )
    from wattpad_crawler.config import load_config

    request.app.state.cfg = load_config(cfg.output_dir)
    return RedirectResponse(url="/config?saved=1", status_code=303)


@router.post("/library/reset/{story_id}")
def library_reset(request: Request, story_id: str) -> RedirectResponse:
    cfg = request.app.state.cfg
    reset_done = False
    archive_db = cfg.output_dir / "archive.sqlite"
    if archive_db.exists():
        repo = ArchiveRepository(cfg.output_dir).connect()
        try:
            with repo.transaction():
                reset_done = repo.reset_story(story_id)
        finally:
            repo.close()

    manifest = Manifest(cfg.output_dir).connect()
    try:
        reset_done = manifest.reset_story(story_id) or reset_done
        if not reset_done:
            raise HTTPException(status_code=404, detail="story not found")
    finally:
        manifest.close()
    return RedirectResponse(url=f"/library?reset={story_id}", status_code=303)


@router.post("/library/bookmark/{story_id}")
def library_bookmark(request: Request, story_id: str) -> RedirectResponse:
    cfg = request.app.state.cfg
    repo = ArchiveRepository(cfg.output_dir).connect()
    try:
        story = repo.get_story(story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        with repo.transaction():
            repo.set_bookmarked(story_id, not story["bookmarked"])
    finally:
        repo.close()
    return RedirectResponse(url=f"/library?bookmarked={story_id}", status_code=303)


@router.post("/library/remove/{story_id}")
def library_remove(request: Request, story_id: str) -> RedirectResponse:
    cfg = request.app.state.cfg
    if not _remove_story_archive(cfg, story_id):
        raise HTTPException(status_code=404, detail="story not found")
    return RedirectResponse(url=f"/library?removed={story_id}", status_code=303)


def _remove_story_archive(cfg, story_id: str) -> bool:
    repo = ArchiveRepository(cfg.output_dir).connect()
    try:
        story = repo.get_story(story_id)
        story_path = _story_path_for_remove(cfg.output_dir, story_id, story)
        if story is None and story_path is None:
            return False
        stories_root = (cfg.output_dir / "stories").resolve()
        resolved_story_path = story_path.resolve() if story_path is not None else None
        if (
            resolved_story_path is not None
            and resolved_story_path.is_relative_to(stories_root)
            and resolved_story_path.exists()
        ):
            shutil.rmtree(resolved_story_path)
        if story is not None:
            with repo.transaction():
                repo.remove_story(story_id)
    finally:
        repo.close()
    return True


def _story_path_for_remove(output_dir: Path, story_id: str, story: dict | None) -> Path | None:
    if story is not None:
        return (
            output_dir
            / "stories"
            / story["author_username"]
            / f"{story_id}_{store.slugify(story['title'])}"
        )
    stories_root = output_dir / "stories"
    if not stories_root.exists():
        return None
    for author_dir in stories_root.iterdir():
        if not author_dir.is_dir():
            continue
        for story_dir in author_dir.iterdir():
            if not story_dir.is_dir() or not story_dir.name.startswith(f"{story_id}_"):
                continue
            meta_path = story_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(meta.get("story_id", "")) == story_id:
                return story_dir
    return None


@router.get("/library/cover/{author}/{dir_name}")
def library_cover(request: Request, author: str, dir_name: str) -> FileResponse:
    cfg = request.app.state.cfg
    target = (cfg.output_dir / "stories" / author / dir_name / "cover.jpg").resolve()
    stories_root = (cfg.output_dir / "stories").resolve()
    if not target.is_relative_to(stories_root) or not target.exists():
        raise HTTPException(status_code=404, detail="cover not found")
    return FileResponse(target, media_type="image/jpeg")


def _resolve_story_dir(cfg, author: str, dir_name: str) -> Path:
    """Resolve a (author, dir_name) request to an absolute path under stories/.
    Rejects path traversal."""
    stories_root = (cfg.output_dir / "stories").resolve()
    target = (cfg.output_dir / "stories" / author / dir_name).resolve()
    if not target.is_relative_to(stories_root):
        raise HTTPException(status_code=400, detail="invalid path")
    if target.exists() and (target / "metadata.json").exists():
        return target

    story_id = dir_name.split("_", 1)[0]
    archive_db = cfg.output_dir / "archive.sqlite"
    if archive_db.exists() and story_id:
        repo = ArchiveRepository(cfg.output_dir).connect()
        try:
            story = repo.get_story(story_id)
        finally:
            repo.close()
        if story and story["author_username"] == author:
            return target
    raise HTTPException(status_code=404, detail="story not found")


def _metadata_from_db(cfg, dir_name: str) -> dict | None:
    archive_db = cfg.output_dir / "archive.sqlite"
    if not archive_db.exists():
        return None
    story_id = dir_name.split("_", 1)[0]
    repo = ArchiveRepository(cfg.output_dir).connect()
    try:
        story = repo.get_story(story_id)
        if story is None:
            return None
        parts = repo.list_parts(story_id)
    finally:
        repo.close()
    story["parts"] = parts
    return story


def _metadata_for_story(cfg, story_dir: Path, dir_name: str) -> dict:
    meta = _metadata_from_db(cfg, dir_name)
    if meta is not None:
        return meta
    return json.loads((story_dir / "metadata.json").read_text(encoding="utf-8"))


def _read_json_or_default(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _comment_count(comments: list[dict]) -> int:
    total = 0
    stack = list(comments)
    while stack:
        comment = stack.pop()
        total += 1
        replies = comment.get("replies") or []
        if isinstance(replies, list):
            stack.extend(reply for reply in replies if isinstance(reply, dict))
    return total


def _chapter_view_data(story_dir: Path, ordinal: int, part: dict) -> dict:
    part_id = part["part_id"]
    prefix = f"{ordinal:02d}_{part_id}_"
    parts_dir = story_dir / "parts"
    json_files = [
        path for path in parts_dir.glob(f"{prefix}*.json") if "_comments-" not in path.name
    ]
    txt_files = list(parts_dir.glob(f"{prefix}*.txt"))
    comments_prefix = f"{ordinal:02d}_{part_id}"
    inline_path = parts_dir / f"{comments_prefix}_comments-inline.json"
    end_path = parts_dir / f"{comments_prefix}_comments-end.json"
    inline_comments = _read_json_or_default(inline_path, [])
    end_comments = _read_json_or_default(end_path, [])

    comments_by_paragraph: defaultdict[str, list[dict]] = defaultdict(list)
    for comment in inline_comments:
        if not isinstance(comment, dict):
            continue
        paragraph_id = comment.get("paragraph_id")
        if paragraph_id:
            comments_by_paragraph[str(paragraph_id)].append(comment)

    paragraphs = []
    if json_files:
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        for paragraph in data.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                continue
            paragraph_id = str(paragraph.get("id") or "")
            comments = comments_by_paragraph.get(paragraph_id, [])
            paragraphs.append({
                "id": paragraph_id,
                "text": paragraph.get("text", ""),
                "html": paragraph.get("html", ""),
                "comments": comments,
                "comment_count": _comment_count(comments),
            })

    body = txt_files[0].read_text(encoding="utf-8") if txt_files else "(missing chapter body)"
    return {
        "title": part.get("title", ""),
        "body": body,
        "paragraphs": paragraphs,
        "end_comments": end_comments,
    }


def _chapter_view_data_from_db(cfg, part: dict) -> dict | None:
    archive_db = cfg.output_dir / "archive.sqlite"
    if not archive_db.exists():
        return None
    part_id = str(part["part_id"])
    repo = ArchiveRepository(cfg.output_dir).connect()
    try:
        paragraphs = []
        comments_by_paragraph = repo.comments_by_paragraph(part_id)
        for paragraph in repo.list_paragraphs(part_id):
            comments = comments_by_paragraph.get(paragraph["paragraph_id"], [])
            paragraphs.append({
                "id": paragraph["paragraph_id"],
                "text": paragraph["text"],
                "html": paragraph["html"],
                "comments": comments,
                "comment_count": _comment_count(comments),
            })
        end_comments = repo.end_comments(part_id)
    finally:
        repo.close()
    return {
        "title": part.get("title", ""),
        "body": part.get("body_text", ""),
        "paragraphs": paragraphs,
        "end_comments": end_comments,
    }


@router.get("/read/{author}/{dir_name}", response_class=HTMLResponse)
def reader_toc(request: Request, author: str, dir_name: str) -> HTMLResponse:
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    meta = _metadata_for_story(cfg, sd, dir_name)
    ords = [int(p["ordinal"]) for p in meta.get("parts", []) if p.get("ordinal") is not None]
    out = sd / "output"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "author": author,
            "dir_name": dir_name,
            "meta": meta,
            "chapter": None,
            "has_epub": any(out.glob("*.epub")) if out.exists() else False,
            "has_html": any(out.glob("*.html")) if out.exists() else False,
            "has_txt": any(out.glob("*.txt")) if out.exists() else False,
            "first_ord": min(ords) if ords else None,
            "last_ord": max(ords) if ords else None,
        },
    )


@router.get("/read/{author}/{dir_name}/{ordinal}", response_class=HTMLResponse)
def reader_chapter(request: Request, author: str, dir_name: str, ordinal: int) -> HTMLResponse:
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    meta = _metadata_for_story(cfg, sd, dir_name)
    parts = sorted(meta.get("parts", []), key=lambda p: p.get("ordinal", 0))
    p = next((q for q in parts if int(q.get("ordinal", 0)) == ordinal), None)
    if p is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    ords = [int(q["ordinal"]) for q in parts]
    prev_ord = max((o for o in ords if o < ordinal), default=None)
    next_ord = min((o for o in ords if o > ordinal), default=None)
    chapter = _chapter_view_data_from_db(cfg, p) or _chapter_view_data(sd, ordinal, p)
    chapter["ordinal"] = ordinal
    chapter["prev_ord"] = prev_ord
    chapter["next_ord"] = next_ord

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "author": author,
            "dir_name": dir_name,
            "meta": meta,
            "chapter": chapter,
        },
    )


@router.get("/library/output/{author}/{dir_name}/{fmt}")
def library_output(request: Request, author: str, dir_name: str, fmt: str) -> FileResponse:
    """Serve the EPUB / HTML / TXT artifact from <story>/output/."""
    if fmt not in ("epub", "html", "txt"):
        raise HTTPException(status_code=404, detail="unknown format")
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    candidates = list((sd / "output").glob(f"*.{fmt}"))
    if not candidates:
        raise HTTPException(status_code=404, detail=f"no .{fmt} artifact")
    media = {"epub": "application/epub+zip", "html": "text/html", "txt": "text/plain"}[fmt]
    return FileResponse(candidates[0], media_type=media, filename=candidates[0].name)
