"""HTTP 路由（契约见设计文档 4.2）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from nekopal import __version__
from nekopal.core import export as audio_export
from nekopal.core.export import ExportError, available_formats, missing_tool
from nekopal.core.models import SynthOptions
from nekopal.core.pipeline import prepare_sentences, synthesize_sentence
from nekopal.server.schemas import InvalidRequest, parse_append_request, parse_read_request
from nekopal.server.state import Runtime


def _invalid(message: str) -> HTTPException:
    return HTTPException(400, {"code": "INVALID_REQUEST", "message": message})


def _safe_name(raw: str) -> str:
    """把用户给的文件名清理成安全的一段（去掉路径分隔符与控制字符）。"""

    cleaned = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "-", raw).strip(" .-")
    return cleaned[:80]


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
            "export_formats": available_formats(),
            # 有几个客户端连着 /api/events —— 「窗口开着吗」的直接答案
            "subscribers": runtime.subscriber_count,
        }

    @router.get("/backends")
    async def backends() -> dict:
        return {"backends": runtime.tts.describe()}

    @router.get("/voices")
    async def voices() -> dict:
        items = await runtime.tts.voices()
        collection = runtime.load_packs()
        # 用户自己的包排在前面（覆盖同名内置音色的优先级更高）
        merged = [pack.to_voice() for pack in collection.voices] + items
        # 能力以后端为唯一来源：音色包不该自己编一份（设计文档 6.7）
        runtime.tts.stamp_capabilities(merged)
        return {
            "default": runtime.tts.resolve_voice_id(
                runtime.settings.default_voice, fallback=runtime.settings.fallback_voice
            ),
            "voices": [voice.to_dict() for voice in merged],
            "pack_problems": [problem.to_dict() for problem in collection.problems],
        }

    @router.get("/packs")
    async def packs(reload: bool = False) -> dict:
        """音色包 / 角色包清单（**只做接口，不内置任何素材**）。"""

        return runtime.load_packs(reload=reload).to_dict()

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

    # -- 全服事件流（跟读窗靠它感知「有人从外部推了新任务」）------------
    @router.get("/events")
    async def events_stream() -> StreamingResponse:
        queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=32)
        runtime.subscribe(queue)

        async def generator():
            try:
                handshake = json.dumps({"ok": True}, ensure_ascii=False)
                yield "event: hello\ndata: {}\n\n".format(handshake)
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"  # 心跳，避免中间层掐断空闲连接
                        continue
                    name = item.get("event", "message")
                    payload = {k: v for k, v in item.items() if k != "event"}
                    yield "event: {}\ndata: {}\n\n".format(
                        name, json.dumps(payload, ensure_ascii=False)
                    )
            finally:
                runtime.unsubscribe(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
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

        # 兜底只对「默认音色」生效：用户**明确选过**的音色不该被悄悄换掉。
        # 踩过：按设计文档把变声命令写在音色包里时，rvc 曾被误判为不可用，
        # 这里静默换成 `say:auto` → 听到的是系统音色，却没有任何提示。
        fallback = (
            runtime.settings.fallback_voice
            if req.voice == runtime.settings.default_voice
            else None
        )
        req.voice = runtime.tts.resolve_voice_id(req.voice, fallback=fallback)
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

        # -- 重复推送去重 --------------------------------------------------
        # 插件可能被 opencode 加载成多个实例，同一轮会发多次同样的内容。
        # 注意：从这里到 tasks.create 之间**不能有 await**，否则并发下会漏判。
        fingerprint = ""
        session_key = req.source.get("session_id")
        if session_key and not req.options.get("no_dedupe"):
            digest = hashlib.sha1(req.text.encode("utf-8")).hexdigest()
            fingerprint = "{}|{}".format(session_key, digest)
            existing_id = runtime.check_duplicate(
                fingerprint, runtime.settings.dedupe_window
            )
            if existing_id is not None:
                runtime.record(
                    "read-deduped",
                    source=str(req.source.get("app") or "unknown"),
                    task_id=existing_id,
                )
                return {
                    "deduped": True,
                    "task_id": existing_id,
                    "sentences": 0,
                    "segments": 0,
                    "start_index": 0,
                }

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
            # 「边写边读」：请求里声明 follow，且服务端没关掉它（设计文档 6.11）
            streaming=bool(req.options.get("follow")) and runtime.settings.follow,
        )
        if task.streaming:
            # 首帧文本也走同一条"只收已定稿句子"的路：
            # 最后一句可能还在长，先留着 —— 否则它一变大就会被判成"前文被改写"。
            task.append(req.text, final=False, default_clean=runtime.settings.clean)
            task.stream.take_announcements()  # 这一批由首帧 plan 播报
        task.start(runtime.tts, runtime.cache)
        if fingerprint:
            runtime.remember_read(fingerprint, task.id)

        source = str(req.source.get("app") or req.source.get("kind") or "unknown")
        runtime.record(
            "read",
            source=source,
            voice=req.voice,
            sentences=len(sentences),
            chars=len(req.text),
        )
        # 广播给所有跟读窗：它们据此自动接上这个任务并开始播放
        runtime.notify(
            "task-created",
            task_id=task.id,
            source=source,
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

    # -- Follow：边写边读（设计文档 6.11）--------------------------------
    @router.post("/read/{task_id}/append")
    async def append(task_id: str, request: Request) -> dict:
        """把「消息到目前为止的全文」并进正在朗读的任务。

        后端自己算增量（`core/append.py`）：**重复发同一份不会有副作用**。
        ``final: true`` 表示这一轮生成结束。返回一个小报告便于调试。
        """

        task = runtime.tasks.get(task_id)
        if task is None:
            raise HTTPException(
                404,
                {"code": "TASK_NOT_FOUND", "message": "任务不存在（可能已被回收）"},
            )

        data = await _json(request)
        try:
            payload = parse_append_request(data)
        except InvalidRequest as exc:
            raise _invalid(str(exc)) from exc

        report = task.append(
            payload["text"],
            final=payload["final"],
            default_clean=runtime.settings.clean,
        )
        runtime.record("append", task_id=task_id, source="follow", **report)
        return report

    @router.post("/export")
    async def export(request: Request) -> dict:
        """把整篇合成结果导出成一个音频文件（设计文档 4.2 / 5.8）。

        两种入口：给 ``task_id`` 复用刚朗读过的缓存；或直接给 ``text`` 现场合成。
        """

        data = await _json(request)
        if not isinstance(data, dict):
            raise _invalid("请求体必须是 JSON 对象")

        settings = runtime.settings
        fmt = str(data.get("format") or settings.export_format)
        try:
            gap = float(data.get("gap", settings.export_gap))
        except (TypeError, ValueError):
            gap = settings.export_gap

        tool = missing_tool(fmt)
        if tool is not None:
            hint = "brew install ffmpeg" if tool == "ffmpeg" else "需要 macOS 自带的 {}".format(tool)
            raise HTTPException(
                409,
                {
                    "code": "TOOL_MISSING",
                    "message": "导出 {} 需要 {}（{}）".format(fmt, tool, hint),
                },
            )

        name = _safe_name(str(data.get("filename") or ""))
        sources: list = []

        task_id = data.get("task_id")
        text = data.get("text")

        if isinstance(task_id, str) and task_id:
            task = runtime.tasks.get(task_id)
            if task is None:
                raise HTTPException(
                    404, {"code": "TASK_NOT_FOUND", "message": "任务不存在或已过期"}
                )
            for audio_id in task.audio_ids:
                segment = runtime.cache.load(audio_id)
                if segment is not None:
                    sources.append(segment.path)
            if not name:
                name = _safe_name(str(task.request.source.get("title") or ""))

        elif isinstance(text, str) and text.strip():
            voice = runtime.tts.resolve_voice_id(
                str(data.get("voice") or settings.default_voice),
                fallback=settings.fallback_voice,
            )
            options = SynthOptions.from_dict(data.get("options"))
            sentences = prepare_sentences(text, {}, settings.clean)
            if not sentences:
                raise HTTPException(
                    422, {"code": "TEXT_TOO_LONG", "message": "清洗后没有可朗读的内容"}
                )
            for sentence in sentences:
                segment, _ = await synthesize_sentence(
                    sentence,
                    voice,
                    options,
                    runtime.tts,
                    runtime.cache,
                    settings.fallback_voice,
                )
                sources.append(segment.path)
        else:
            raise _invalid("需要提供 `task_id` 或 `text` 之一")

        if not sources:
            raise HTTPException(
                409, {"code": "EXPORT_FAILED", "message": "没有可导出的音频段"}
            )

        output_base = settings.export_dir / (name or "nekopal-export")
        try:
            result = audio_export.assemble(sources, output_base, fmt=fmt, gap=gap)
        except ExportError as exc:
            raise HTTPException(
                409, {"code": "EXPORT_FAILED", "message": str(exc)}
            ) from exc

        result["filename"] = Path(result["path"]).name
        runtime.record(
            "export",
            format=fmt,
            segments=result["segments"],
            bytes=result["bytes"],
            path=result["path"],
        )
        return result

    @router.get("/characters")
    async def characters(reload: bool = False) -> dict:
        # Phase 3 才开始渲染形象；现在只负责**扫描用户自己放进来的包**
        collection = runtime.load_packs(reload=reload)
        return {
            "characters": [pack.to_dict() for pack in collection.characters],
            "problems": [problem.to_dict() for problem in collection.problems],
        }

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
