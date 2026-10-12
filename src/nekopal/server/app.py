"""FastAPI 应用装配。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from nekopal import __version__
from nekopal.config import Settings
from nekopal.server.routes import build_router
from nekopal.server.state import Runtime


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    runtime = Runtime.build(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.warmup()
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(title="NekoPal", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """把错误统一成 ``{code, message}``（设计文档 4.2 错误码）。"""

        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            body = detail
        else:
            body = {"code": "HTTP_ERROR", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=body)

    app.include_router(build_router(runtime), prefix="/api")

    web_dir = runtime.settings.web_dir
    if web_dir is not None and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
