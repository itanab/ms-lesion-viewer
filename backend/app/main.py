"""Application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from . import APP_NAME, __version__
from .api.routes import router
from .config import Settings
from .context import AppContext
from .errors import install_error_handling
from .worker import JobWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


class SPAStatic(StaticFiles):
    """Serve the built frontend; unknown non-API paths fall back to index.html."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404 and not path.startswith("api/"):
                return FileResponse(Path(self.directory) / "index.html")
            raise


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    ctx = AppContext.create(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.db.fail_interrupted_preparation()
        worker = JobWorker(ctx)
        if settings.run_worker:
            worker.start()
        app.state.worker = worker
        yield
        worker.stop()
        ctx.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.ctx = ctx
    install_error_handling(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["*"], expose_headers=["Content-Disposition", "X-Request-ID"])
    app.include_router(router)
    if settings.frontend_dist and Path(settings.frontend_dist).is_dir():
        app.mount("/", SPAStatic(directory=str(settings.frontend_dist), html=True), name="frontend")
    return app


def app_factory() -> FastAPI:
    return create_app()
