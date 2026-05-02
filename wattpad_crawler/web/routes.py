import json
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sse_starlette.sse import EventSourceResponse

from wattpad_crawler.api.user import fetch_library, fetch_list_story_ids
from wattpad_crawler.archive.state import Manifest
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
    """Write/update the cookie line in _config.toml. Preserves other settings."""
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
        config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f'cookie = "{cookie}"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
            encoding="utf-8",
        )


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


@router.post("/setup")
def setup_post(request: Request, cookie: str = Form(...)) -> RedirectResponse:
    cfg = request.app.state.cfg
    _save_cookie(cfg.output_dir, cookie)
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
async def job_stream(request: Request, job_id: str, after: int = 0):
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        import asyncio
        index = after
        while True:
            if await request.is_disconnected():
                break
            new_events = job.snapshot_events(index)
            for ev in new_events:
                index += 1
                yield {
                    "data": json.dumps({"kind": ev.kind, "data": ev.data, "ts": ev.timestamp})
                }
            if job.status.value in ("done", "failed"):
                yield {
                    "data": json.dumps({
                        "kind": "__status__",
                        "data": {"status": job.status.value, "error": job.error},
                    })
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
