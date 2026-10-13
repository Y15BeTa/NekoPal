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
        # 把主窗**选过的音色**捞回来 ✗ ——
        # 不然后端一重启就退回默认音色（界面上明明选着要楽奈，说出来却是晓晓 ✗）。
        # ⚠️ 这里只**读文件 + 赋值** ✗ —— 绝不 spawn 任何子进程 ✗
        #    （在 lifespan 里 spawn 会把后端启动卡死、整个 App 白屏 ✗，踩过 ✗）
        _restore_ui_voice(runtime)
        await runtime.warmup()
        # ⚠️ **不要在这里拉起角色窗口** ✗ —— 踩过：在 lifespan 里 spawn 子进程
        #    会把后端的启动卡住 ✗ → 后端永远不开始服务 ✗ → **整个 App 白屏** ✗。
        #    角色窗口由 `run_app` 在后端就绪之后拉起 ✗（那边一直是好的 ✗），
        #    主窗里切换开关时由 `PUT /api/ui` 调 `pet_manager` 处理 ✗。
        try:
            yield
        finally:
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

def _restore_ui_voice(runtime: "Runtime") -> None:
    """把 `ui.json` 里记着的**音色与设置**，套回本次后端进程 ✗。

    为什么需要：主窗那些控件只是 `PUT /api/settings` ✗，
    而那是**内存**里的值 ✗ —— 后端一重启就没了 ✗
    （"界面选着要楽奈、说出来却是晓晓 ✗"就是这么来的 ✗）。
    """

    try:
        import json as _json

        from nekopal.packs import config_dir

        data = _json.loads((config_dir() / "ui.json").read_text(encoding="utf-8")) or {}

        voice = str(data.get("voice") or "").strip()
        if voice:
            runtime.settings.default_voice = runtime.tts.resolve_voice_id(voice)

        # 🌟 设置面板那几项 ✗（类型不对就当没写 ✗）
        for key, cast in (
            ("auto_read", bool),
            ("follow", bool),
            ("auto_scroll", str),
            ("prefetch", int),
            ("cache_max_mb", int),
            ("line_source", str),
            ("pitch", float),
            # 注意：`paw_cursor` / `gaze_follow` **不在这里** ✗ ——
            # 它们只存在 `ui.json` 里、不是 `Settings` 字段 ✗，
            # 而且 `/api/ui` 每次都现读文件 ✅（角色窗 3 秒轮询一次就拿到了 ✗）。
        ):
            if key in data and data[key] is not None:
                try:
                    setattr(runtime.settings, key, cast(data[key]))
                except (TypeError, ValueError):
                    pass
    except Exception:  # noqa: BLE001 - 没有 / 坏了就用默认，不是错误 ✗
        pass
