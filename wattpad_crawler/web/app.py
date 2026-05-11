from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wattpad_crawler.config import Config

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"


def build_app(cfg: Config) -> FastAPI:
    """Construct the FastAPI app. cfg is stashed on app.state for routes to use."""
    app = FastAPI(title="Wattpad Crawler", docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from wattpad_crawler.web.runner import JobManager, JobRunner
    app.state.job_manager = JobManager()
    app.state.job_runner = JobRunner(app.state.job_manager)

    @app.get("/_health")
    def health() -> dict:
        return {"status": "ok"}

    from wattpad_crawler.web.routes import router as main_router
    app.include_router(main_router)

    return app
