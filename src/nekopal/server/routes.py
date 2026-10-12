"""HTTP 路由（契约见设计文档 4.2）。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from nekopal import __version__
from nekopal.core.models import SynthOptions
from nekopal.core.pipeline import prepare_sentences
from nekopal.server.schemas import InvalidRequest, parse_read_request
from nekopal.server.state import Runtime


def _invalid(message: str) -> HTTPException:
    return HTTPException(400, {"code": "INVALID_REQUEST", "message": message})


def build_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    async def _json(request: Request) -> Any:
        try:
            return await request.json()
        except Exception as exc:  # noqa: BLE001
            raise _invalid("请求体不是合法 JSON") from exc

    # -- 元信息 ----------------------------------------------------------
    @router.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "version": __version__,
            # 对外暴露**已解析**的默认音色（含离线兜底），客户端不需要理解 "auto" 这种占位符
            "default_voice": runtime.tts.resolve_voice_id(
                runtime.settings.default_voice, fallback=runtime.settings.fallback_voice
            ),
            "fallback_voice": runtime.settings.fallback_voice,
            "backends": runtime.tts.describe(),
            "cache": runtime.cache.stats(),
        }

    @router.get("/backends")
    async def backends() -> dict:
        return {"backends": runtime.tts.describe()}

    @router.get("/voices")
    async def voices() -> dict:
        items = await runtime.tts.voices()
        return {
            "default": runtime.tts.resolve_voice_id(
                runtime.settings.default_voice, fallback=runtime.settings.fallback_voice
            ),
            "voices": [voice.to_dict() for voice in items],
        }

    @router.get("/capture/sources")
    async def capture_sources() -> dict:
        return {"sources": runtime.captures.describe()}

    # -- 活动记录（排查集成问题用；也是未来「最近推送」界面的数据源）------
    @router.get("/activity")
    async def get_activity(limit: int = 30) -> dict:
        size = max(1, min(int(limit), 100))
        return {"activity": list(runtime.activity)[:size]}

    @router.post("/activity", status_code=201)
    async def post_activity(request: Request) -> dict:
        data = await _json(request)
        if not isinstance(data, dict):
            raise _invalid("请求体必须是 JSON 对象")
        return runtime.record(
            str(data.get("event") or "unknown"),
            source=str(data.get("source") or "unknown"),
            detail=data.get("detail"),
        )

    # -- 设置 ------------------------------------------------------------
    @router.get("/settings")
    async def get_settings() -> dict:
        return runtime.settings.to_dict()

    @router.put("/settings")
    async def put_settings(request: Request) -> dict:
        data = await _json(request)
        if not isinstance(data, dict):
            raise _invalid("请求体必须是 JSON 对象")
        settings = runtime.settings
        if isinstance(data.get("default_voice"), str):
            settings.default_voice = runtime.tts.resolve_voice_id(data["default_voice"])
        if isinstance(data.get("rate"), (int, float)):
            settings.rate = float(data["rate"])
        if isinstance(data.get("clean"), list):
            settings.clean = [str(item) for item in data["clean"]]
        if isinstance(data.get("auto_scroll"), str):
            settings.auto_scroll = data["auto_scroll"]
        return settings.to_dict()

    # -- 朗读任务 --------------------------------------------------------
    @router.post("/read", status_code=201)
    async def read(request: Request) -> dict:
        data = await _json(request)
        try:
            req = parse_read_request(data, runtime.settings.default_voice)
        except InvalidRequest as exc:
            raise _invalid(str(exc)) from exc

        req.voice = runtime.tts.resolve_voice_id(
            req.voice, fallback=runtime.settings.fallback_voice
        )
        options = SynthOptions.from_dict(req.options)
        sentences = prepare_sentences(req.text, req.options, runtime.settings.clean)
        if not sentences:
            raise HTTPException(
                422, {"code": "TEXT_TOO_LONG", "message": "清洗后没有可朗读的内容"}
            )

        try:
            start_index = int(req.options.get("start_index") or 0)
        except (TypeError, ValueError):
            start_index = 0
        start_index = max(0, min(start_index, len(sentences) - 1))

        if req.options.get("interrupt", True):
            runtime.tasks.cancel_all()

        task = runtime.tasks.create(
            req,
            sentences,
            options,
            queue_size=runtime.settings.prefetch + 2,
            start_index=start_index,
            fallback_voice=runtime.tts.resolve_voice_id(
                runtime.settings.fallback_voice, fallback=None
            ),
        )
        task.start(runtime.tts, runtime.cache)

        runtime.record(
            "read",
            source=str(req.source.get("app") or req.source.get("kind") or "unknown"),
            voice=req.voice,
            sentences=len(sentences),
            chars=len(req.text),
        )

        return {
            "task_id": task.id,
            "segments": len(sentences),
            "sentences": len(sentences),
            "start_index": start_index,
        }

    @router.get("/read/{task_id}/events")
    async def events(task_id: str) -> StreamingResponse:
        task = runtime.tasks.get(task_id)
        if task is None:
            raise HTTPException(
                404, {"code": "TASK_NOT_FOUND", "message": "任务不存在或已过期"}
            )

        async def generator():
            async for event in task.events():
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield "event: {}\ndata: {}\n\n".format(event["type"], payload)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.delete("/read/{task_id}", status_code=204)
    async def stop(task_id: str) -> Response:
        task = runtime.tasks.get(task_id)
        if task is None:
            raise HTTPException(
                404, {"code": "TASK_NOT_FOUND", "message": "任务不存在或已过期"}
            )
        task.cancel()
        return Response(status_code=204)

    # -- 音频 ------------------------------------------------------------
    @router.get("/audio/{audio_id}")
    async def audio(audio_id: str) -> FileResponse:
        segment = runtime.cache.load(audio_id)
        if segment is None:
            raise HTTPException(
                404, {"code": "AUDIO_NOT_FOUND", "message": "音频不存在或已被淘汰"}
            )
        return FileResponse(str(segment.path), media_type=segment.media_type)

    # -- 预留：Phase 2 / Phase 3（设计文档 4.2）-------------------------
    @router.post("/read/{task_id}/append")
    async def append(task_id: str) -> dict:
        raise HTTPException(
            501,
            {"code": "NOT_IMPLEMENTED", "message": "自动续读（Follow）将在 Phase 2 提供"},
        )

    @router.post("/export")
    async def export() -> dict:
        raise HTTPException(
            501,
            {"code": "NOT_IMPLEMENTED", "message": "音频导出将在 Phase 2 提供"},
        )

    @router.get("/characters")
    async def characters() -> dict:
        # Phase 3 才有内容；现在返回空数组，前端据此优雅降级
        return {"characters": []}

    @router.get("/avatar/capabilities")
    async def avatar_capabilities() -> dict:
        return {"avatars": runtime.avatars.describe()}

    @router.post("/avatar/state")
    async def avatar_state() -> dict:
        raise HTTPException(
            501,
            {"code": "NOT_IMPLEMENTED", "message": "角色层将在 Phase 3 提供"},
        )

    return router
