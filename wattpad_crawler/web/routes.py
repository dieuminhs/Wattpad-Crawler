import dataclasses
import json
import os
import threading
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sse_starlette.sse import EventSourceResponse

from wattpad_crawler.api.user import fetch_library, fetch_list_story_ids
from wattpad_crawler.archive.state import Manifest
from wattpad_crawler.auth import AuthError, validate_cookie
from wattpad_crawler.client import RateLimitedClient
from wattpad_crawler.jobs import (
    ResolveError,
    archive_many,
    archive_story,
    resolve_story_id,
)
from wattpad_crawler.web.library_browser import scan_library

router = APIRouter()


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
                new_lines.append(f'cookie = "{cookie}"')
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f'cookie = "{cookie}"')
        new_text = "\n".join(new_lines) + "\n"
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        new_text = (
            f'cookie = "{cookie}"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n'
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
            sid = resolve_story_id(target)
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
    else:
        raise HTTPException(status_code=400, detail=f"unknown kind: {kind}")

    return RedirectResponse(url=f"/jobs/{job.job_id}", status_code=303)


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
    entries = scan_library(cfg.output_dir)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="library.html",
        context={"entries": entries},
    )


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
    if not target.exists() or not (target / "metadata.json").exists():
        raise HTTPException(status_code=404, detail="story not found")
    return target


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


@router.get("/read/{author}/{dir_name}", response_class=HTMLResponse)
def reader_toc(request: Request, author: str, dir_name: str) -> HTMLResponse:
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
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
        },
    )


@router.get("/read/{author}/{dir_name}/{ordinal}", response_class=HTMLResponse)
def reader_chapter(request: Request, author: str, dir_name: str, ordinal: int) -> HTMLResponse:
    cfg = request.app.state.cfg
    sd = _resolve_story_dir(cfg, author, dir_name)
    meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
    parts = sorted(meta.get("parts", []), key=lambda p: p.get("ordinal", 0))
    p = next((q for q in parts if int(q.get("ordinal", 0)) == ordinal), None)
    if p is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    ords = [int(q["ordinal"]) for q in parts]
    prev_ord = max((o for o in ords if o < ordinal), default=None)
    next_ord = min((o for o in ords if o > ordinal), default=None)
    chapter = _chapter_view_data(sd, ordinal, p)
    chapter["prev_ord"] = prev_ord
    chapter["next_ord"] = next_ord

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "author": author,
            "dir_name": dir_name,
            "meta": meta,
            "chapter": {
                "title": p.get("title", ""),
                "body": body,
                "prev_ord": prev_ord,
                "next_ord": next_ord,
            },
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
