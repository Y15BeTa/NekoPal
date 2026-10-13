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
        # 角色窗口（桌宠）由**后端**负责生命周期 ✗ —— 主窗的「人物形象」开关说了算 ✗
        from nekopal import pet_manager

        pet_manager.ensure(pet_manager.show_avatar_wanted())
        try:
            yield
        finally:
            pet_manager.stop()
            await runtime.aclose()

    app = FastAPI(title="NekoPal", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime

    @app.middleware("http")
    async def no_cache_web_assets(request: Request, call_next):
        """前端资源与 **API 响应**一律禁止缓存。

        两件事都踩过：

        1. 前端资源：改完 `web/` 后浏览器继续用旧文件，出现「界面更新了但行为还是旧的」
           —— 极难排查。
        2. **API 响应**（2026-10-13 补 ✗）：原来只给非 `/api` 路径加头 ✗，
           于是 `/api/avatar` 没有任何缓存头 ✗ → WKWebView **启发式缓存**了它 ✗ →
           角色窗一直拿着**加了 `outfits` 字段之前**的旧响应 ✗ →
           菜单里换装/动作/表情**全是空的**（查了很久 ✗）。
        """

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

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
