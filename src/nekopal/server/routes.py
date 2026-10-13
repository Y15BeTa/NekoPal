"""HTTP 路由（契约见设计文档 4.2）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from nekopal import __version__
from nekopal.core import export as audio_export
from nekopal.core.export import ExportError, available_formats, missing_tool
from nekopal.core.models import SynthOptions
from nekopal.core.pipeline import prepare_sentences, synthesize_sentence
from nekopal.packs import config_dir, load_packs
from nekopal.server.schemas import InvalidRequest, parse_append_request, parse_read_request
from nekopal.server.state import Runtime


def _ui_file() -> Path:
    return config_dir() / "ui.json"


def _load_ui() -> dict:
    """主窗与角色窗口**共享**的偏好（形象开关 / 选哪个形象 ✗）。

    为什么放在后端而不是 localStorage：角色窗口是**另一个进程** ✗，
    localStorage 它读不到。所以两边都问后端要 ✗
    （音色不在这儿 —— 它由主窗的 localStorage + `/api/packs` 已经管好了 ✗）。
    """

    #: `voice` 存的是**主窗那个下拉选的音色** ✗ ——
    #: 后端重启也不该丢（默认值不是用户的意愿 ✗）
    defaults = {
        "show_avatar": True,
        "character": None,
        "outfit": None,
        "voice": None,
        # 🌟 设置面板里的那几项也存这里 ✗ —— 不然后端一重启就打回默认 ✗
        "auto_read": True,
        "follow": True,
        "auto_scroll": "center",
        "prefetch": 3,
        "cache_max_mb": 500,
        # 🌟 点击角色时，台词从哪来 ✗：`pool` 抽预生成的（快 ✅）/ `live` 每次现编（慢 ✗）
        "line_source": "pool",
    }
    try:
        data = json.loads(_ui_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 没有 / 坏了都当默认值
        return defaults
    if isinstance(data, dict):
        defaults.update({k: v for k, v in data.items() if k in defaults})
    return defaults


def _save_ui(data: dict) -> dict:
    merged = _load_ui()
    merged.update({k: v for k, v in (data or {}).items() if k in merged})
    try:
        _ui_file().parent.mkdir(parents=True, exist_ok=True)
        _ui_file().write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - 写不进去也不该让请求失败
        pass
    return merged


def _outfit_label(folder: str) -> str:
    """把形象目录名翻成人话（右键菜单里要显示 ✗）。"""

    name = folder.replace("live2d_rana_003_", "").replace("adv_", "")
    known = {
        "live_01": "演出服",
        "live_01_low": "演出服·低模",
        "casual_spring_01": "便服·春",
        "school_winter_jhs": "校服·冬",
        "school_summer_jhs": "校服·夏",
    }
    label = known.get(name, name)
    if folder.startswith("adv_"):
        label += "（ADV）"
    return label


def _list_outfits(pack_dir: Path) -> list:
    """扫出角色包里所有可用的形象，并带上各自的**动作分组 / 表情**。

    为什么要连动作/表情一起报：右键菜单的「动作 / 表情」子菜单得知道
    这个形象到底有什么 ✗ —— 不同形象差别很大
    （实测：`live_*` 只有 9 个动作、**0 个表情**；`adv_*` 有 57 个动作 + 19 个表情 ✗）。
    """

    outfits = []
    model_root = pack_dir / "model"
    if not model_root.is_dir():
        return outfits

    for model_file in sorted(model_root.glob("*/*.model3.json")):
        try:
            data = json.loads(model_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 单个形象读坏了不该影响整体
            continue
        refs = data.get("FileReferences") or {}
        motions = refs.get("Motions") or {}
        outfits.append(
            {
                "folder": model_file.parent.name,
                "label": _outfit_label(model_file.parent.name),
                "path": str(model_file.relative_to(pack_dir)),
                # 动作按「组」报，每组有多少个 —— 菜单里按组列出更好点
                "motions": [
                    {"group": g, "count": len(v)} for g, v in motions.items()
                ],
                "expressions": [
                    e.get("Name") for e in (refs.get("Expressions") or []) if e.get("Name")
                ],
                "physics": bool(refs.get("Physics")),
            }
        )
    return outfits


def _invalid(message: str) -> HTTPException:
    return HTTPException(400, {"code": "INVALID_REQUEST", "message": message})


def _outside_file(root: Path, relative: str, code: str) -> FileResponse:
    """把 ``root/<relative>`` 当文件发出去，并**挡住目录穿越**。

    ``relative`` 来自 URL、用户可控 —— 不校验的话 ``../../.ssh/id_rsa`` 就被读走了 ✗。

    做法：``resolve()`` 之后必须仍**在 root 里面**。注意用**路径段**比较
    （``root in target.parents``），不要用字符串前缀 ——
    ``/a/bc`` 会被 ``startswith("/a/b")`` 误判成"在里面" ✗。
    """

    root = root.resolve()
    target = (root / relative).resolve()

    if target != root and root not in target.parents:
        raise HTTPException(403, {"code": code + "_FORBIDDEN", "message": "路径越界"})
    if not target.is_file():
        raise HTTPException(404, {"code": code + "_MISSING", "message": "文件不存在"})

    media, _ = mimetypes.guess_type(str(target))
    return FileResponse(str(target), media_type=media or "application/octet-stream")


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
            # ⚠️ 顺手存一份 ✗ —— 光改内存的话，后端一重启就回到默认音色 ✗
            #    （用户会看到"我在界面上选了要楽奈，说出来的却是晓晓 ✗"✗）
            _save_ui({"voice": settings.default_voice})
        if isinstance(data.get("rate"), (int, float)):
            settings.rate = float(data["rate"])
        if isinstance(data.get("clean"), list):
            settings.clean = [str(item) for item in data["clean"]]
        if isinstance(data.get("auto_scroll"), str):
            settings.auto_scroll = data["auto_scroll"]
        # 🌟 这几个是「设置面板」用的 ✗（以前后端有、界面没有 ✗）
        if isinstance(data.get("auto_read"), bool):
            settings.auto_read = data["auto_read"]
        if isinstance(data.get("follow"), bool):
            settings.follow = data["follow"]
        if isinstance(data.get("prefetch"), (int, float)):
            settings.prefetch = max(0, min(10, int(data["prefetch"])))
        if isinstance(data.get("cache_max_mb"), (int, float)):
            settings.cache_max_mb = max(50, min(10000, int(data["cache_max_mb"])))
        # ⚠️ 一起存进 ui.json ✗ —— 只改内存的话，后端一重启就回到默认值 ✗
        #    （音色那个 bug 就是这么来的 ✗）
        _save_ui(
            {
                k: getattr(settings, k)
                for k in ("auto_read", "follow", "prefetch", "cache_max_mb", "rate", "auto_scroll")
                if k in data
            }
        )
        return settings.to_dict()

    @router.delete("/cache")
    async def clear_cache() -> dict:
        """清空音频缓存（设置面板上的「清缓存」）。"""

        before = runtime.cache.stats()
        removed = 0
        # `evict()` 是按「上限」删，不是全清；这里要的是全清 → 直接把上限压到 0。
        keep = runtime.cache.max_mb
        try:
            runtime.cache.max_mb = 0
            removed = runtime.cache.evict()
        finally:
            runtime.cache.max_mb = keep
        runtime.record("cache-cleared", files=removed, bytes=before.get("bytes"))
        return {"cleared": True, "files": removed, "bytes": before.get("bytes", 0)}

    @router.get("/cache")
    async def cache_info() -> dict:
        """缓存用了多少 / 上限多少（设置面板显示用）。"""

        info = runtime.cache.stats()
        info["max_mb"] = runtime.settings.cache_max_mb
        return info

    # -- 朗读任务 --------------------------------------------------------
    @router.post("/read", status_code=201)
    async def read(request: Request) -> dict:
        data = await _json(request)
        try:
            req = parse_read_request(data, runtime.settings.default_voice)
        except InvalidRequest as exc:
            raise _invalid(str(exc)) from exc

        # 🌟 自动朗读开关（`auto_read` ✗）：
        #   关掉之后，**外部推送**（opencode 插件等）只被"收到"，不自动出声 ✗。
        #   ⚠️ 只拦外部来源 ✗ —— 你自己在界面上点「朗读」照常播 ✅
        if not runtime.settings.auto_read and str(req.source.get("app") or "") != "web":
            runtime.record(
                "read-skipped",
                source=str(req.source.get("app") or "unknown"),
                reason="auto_read_off",
            )
            return {
                "skipped": True,
                "reason": "auto_read_off",
                "task_id": None,
                "sentences": 0,
                "segments": 0,
                "start_index": 0,
            }

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

    # -- 远程控制（给「全局快捷键」用）-----------------------------------
    @router.post("/control", status_code=202)
    async def control(request: Request) -> dict:
        """远程控制跟读窗：暂停 / 继续 / 停止 / 上下一句。

        **为什么不由后端直接暂停**：声音在跟读窗（浏览器）里 ——
        后端只能**广播**，由前端真正去 pause/resume。
        这样任何东西（快捷键 / 脚本 / 别的程序）都能控制朗读，
        **不用先把 NekoPal 窗口切到前面**（这正是它的用途）。
        """

        data = await _json(request)
        action = str((data or {}).get("action") or "toggle")
        allowed = ("toggle", "pause", "resume", "stop", "next", "prev")
        if action not in allowed:
            raise _invalid("action 只能是 {}".format(" / ".join(allowed)))

        runtime.record("control", action=action)
        runtime.notify("control", action=action)
        return {"ok": True, "action": action, "subscribers": runtime.subscriber_count}

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

    # -- 角色形象（Live2D）------------------------------------------------
    # 为什么由后端中转：模型和 Cubism Core 都在**仓库外**（`~/.config/nekopal/`），
    # 而 `StaticFiles` 只挂了 `web/` —— 前端够不着。
    # 顺带把 Core 挡在这一层：它**不进仓库**（见 characters/README.md 的红线）。

    # -- UI 偏好（主窗 ↔ 角色窗口 共享 ✗）---------------------------------
    # -- 口型同步（主窗 → 角色窗口 ✗）-------------------------------------
    # 音频在主跟读窗的 <audio> 里 ✗，角色窗口是**另一个进程**、够不着 ✗。
    # 所以：主窗用 WebAudio 算出电平 → POST 过来 → 角色窗轮询着张嘴 ✅
    _lip = {"level": 0.0, "at": 0.0}

    @router.post("/lip")
    async def post_lip(request: Request) -> dict:
        """主窗上报音频电平（0..1 ✗）。高频、fire-and-forget ✗。"""

        try:
            payload = await request.json()
            level = float(payload.get("level", 0.0))
        except Exception:  # noqa: BLE001
            raise _invalid("要传 {\"level\": 0..1}") from None
        _lip["level"] = max(0.0, min(1.0, level))
        _lip["at"] = time.monotonic()
        return {"ok": True}

    @router.get("/lip")
    async def get_lip() -> dict:
        """角色窗取电平 ✗。超过 0.4 秒没更新就当静音 ✗（停止朗读时会归位 ✗）。"""

        stale = (time.monotonic() - _lip["at"]) > 0.4
        return {"level": 0.0 if stale else _lip["level"], "stale": stale}

    @router.get("/ui")
    async def get_ui() -> dict:
        """主窗与角色窗口共享的偏好：要不要开人物、选哪个形象。"""

        return _load_ui()

    @router.put("/ui")
    async def put_ui(request: Request) -> dict:
        """合并式更新（只传想改的字段 ✓）。

        改完立刻同步角色窗口的**开关** ✗ —— 勾上就拉起、取消就收掉，
        不用等下次开 App ✅（谁负责起进程？`nekopal.pet_manager` ✗）。
        """

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise _invalid("JSON 格式不对") from None
        if not isinstance(payload, dict):
            raise _invalid("要传一个对象")

        merged = _save_ui(payload)
        # ⚠️ 这里**故意不**去 spawn 角色窗口 ✗ —— 那只猫是 `run_app` 拉起的 ✗，
        #    `pet_manager` 并不知道它 ✗ → 在这里 ensure 会**再拉一只** ✗（两只叠一起 ✗）。
        #    所以：「取消勾选 → 角色窗自己退出」由角色窗轮询 `show_avatar` 完成 ✅；
        #    「重新勾上 → 它回来」要等**下次开 App** ✗（已知限制，不装死 ✗）。
        return merged

    @router.get("/avatar")
    async def avatar() -> dict:
        """当前可用的角色形象 + 每个形象的**动作分组 / 表情**（右键菜单要用）。

        只报**模型文件齐全**的角色包 —— 缺形象的会降级成"只有声音"，
        朗读不受影响（设计文档 4.5）。
        """

        ready = [c for c in load_packs().characters if c.model_exists]
        out = []
        for c in ready:
            # ⚠️ `avatar_model` 是**绝对路径**（packs 里 resolve 过）。直接丢给前端会拼成
            #    `/api/avatar/rana//Users/...` ✗，而且把本机路径泄露到接口里 ✗。
            #    所以在这儿换成「相对角色包的路径」，并顺手给出可直接用的 url。
            rel = None
            if c.avatar_model:
                try:
                    rel = str(Path(c.avatar_model).relative_to(c.path))
                except ValueError:
                    rel = None
            out.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "type": c.avatar_type,
                    "model": rel,
                    "url": "/api/avatar/{}/{}".format(c.id, rel) if rel else None,
                    "lipsync": {"mode": c.lipsync_mode, "param": c.lipsync_param},
                    "expressions": dict(c.expressions),
                    "outfits": _list_outfits(Path(c.path)),
                }
            )
        return {
            "characters": out,
            "active": out[0]["id"] if out else None,
            # 主窗选的（ui.json ✗）—— 角色窗口优先用它们 ✗
            "active_character": _load_ui().get("character"),
            "active_outfit": _load_ui().get("outfit"),
            "show_avatar": bool(_load_ui().get("show_avatar", True)),
        }

    def _avatar_voice(character_id: Optional[str]) -> str:
        """角色说话用哪个音色 ✗。

        优先级（**用户明确选过的优先** ✗）：

        1. `ui.json` 里的 `voice` ✗ —— 主窗顶栏那个下拉选的就是它
        2. **角色包自己声明的 `voice:`** ✗（以前它是个摆设 ✗，从没被用过 ✗）
        3. 后端默认值 ✗

        ⚠️ 为什么要持久化第 1 条：早先主窗只是 `PUT /api/settings` ✗，
        而那是**内存**里的值 ✗ —— 后端一重启就回到 "晓晓" ✗，
        用户却明明在界面上选了要楽奈 ✗（"我从来没改过它 ✗"就是这么来的 ✗）。
        """

        ui_voice = str(_load_ui().get("voice") or "").strip()
        if ui_voice:
            return runtime.tts.resolve_voice_id(
                ui_voice, fallback=runtime.settings.fallback_voice
            )

        pack_voice = ""
        if character_id:
            try:
                for pack in load_packs().characters:
                    if pack.id == character_id:
                        pack_voice = pack.voice or ""
                        break
            except Exception:  # noqa: BLE001
                pass

        return runtime.tts.resolve_voice_id(
            pack_voice or runtime.settings.default_voice,
            fallback=runtime.settings.fallback_voice,
        )

    # -- 角色语音（点一下角色时说一句 ✗）-----------------------------------
    #: 默认台词。角色包可以在 character.yaml 里用 `voice_lines:` 覆盖 ✗。
    #: 短句很重要 ✗：RVC 变声一句约 1.7 秒，太长会等 ✗（同句第二次走缓存 ✗）
    _DEFAULT_LINES = ["嗯？", "在的哦～", "怎么啦？", "喵～", "我在听呢", "嗯嗯～"]

    def _voice_lines(character_id: Optional[str]) -> list:
        """取某个角色的台词（角色包的 `voice_lines:` 优先 ✗）。"""

        if character_id:
            try:
                import yaml  # 延迟导入：只有用到这里才需要 ✗

                path = config_dir() / "characters" / character_id / "character.yaml"
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                lines = data.get("voice_lines")
                if isinstance(lines, list) and lines:
                    return [str(x) for x in lines if str(x).strip()]
            except Exception:  # noqa: BLE001 - 没有 / 坏了就用默认 ✗
                pass
        return list(_DEFAULT_LINES)

    @router.get("/avatar/lines")
    async def avatar_lines(character: str = "") -> dict:
        """这个角色有哪些台词（角色窗口开窗时取一次 ✗）。"""

        cid = character or _load_ui().get("character") or None
        return {"lines": _voice_lines(cid)}

    # -- 预生成台词池 ------------------------------------------------------
    # 为什么有这套 ✗：`/api/avatar/chat` 每次点击都打一次 LLM ✗，要等好几秒 ✗。
    # 改成**一次生成几十条存档**✗，点击时从池子里抽 ✅ —— 零延迟、也不像"在等程序"✗。
    #
    # ⚠️ 池子 = profile.md（私人信息）的衍生物 ✗ → 存在**仓库外**✗：
    #    `~/.config/nekopal/characters/<id>/lines.json`

    @router.get("/lines")
    async def lines_get(character: str = "") -> dict:
        """台词池现状（有几条 / 什么时候生成的 / 过时了没 ✗）。"""

        from nekopal import chat as chat_mod

        cid = character or _load_ui().get("character") or None
        return chat_mod.lines_status(cid)

    @router.post("/lines")
    async def lines_generate(request: Request) -> dict:
        """**一次生成一整批**台词并存档 ✗（要等几秒到几十秒 ✗）。

        ⚠️ 必须丢到线程里跑 ✗ —— 它是同步阻塞的 HTTP 调用，
        直接 await 会把整个事件循环卡住（跟读窗也会没反应 ✗）。
        """

        from nekopal import chat as chat_mod

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        cid = (str(payload.get("character") or "").strip()
               or _load_ui().get("character") or None)
        count = payload.get("count") or chat_mod.DEFAULT_BATCH

        try:
            data = await asyncio.to_thread(chat_mod.generate_lines, cid, count)
        except Exception as exc:  # noqa: BLE001 - 失败要让界面上看得见 ✗
            runtime.record("lines-generate-failed", error=str(exc)[:160])
            raise HTTPException(
                502, {"code": "LINES_FAILED", "message": str(exc)[:200]}
            ) from None

        runtime.record("lines-generated", count=data["count"], model=data["model"])
        return data

    @router.put("/lines")
    async def lines_put(request: Request) -> dict:
        """**整份替换**台词池 ✗ —— 给你在界面上改 / 删掉那几句不喜欢的 ✅。"""

        from nekopal import chat as chat_mod

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise _invalid("JSON 格式不对") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
            raise _invalid("要传 {\"lines\": [\"...\", ...]}")

        cid = (str(payload.get("character") or "").strip()
               or _load_ui().get("character") or None)
        lines = [str(x) for x in payload["lines"] if str(x).strip()]
        data = chat_mod.save_lines(cid, lines)
        runtime.record("lines-edited", count=data["count"])
        return data

    @router.delete("/lines")
    async def lines_clear(character: str = "") -> dict:
        """清空台词池 ✗。"""

        from nekopal import chat as chat_mod

        cid = character or _load_ui().get("character") or None
        data = chat_mod.clear_lines(cid)
        runtime.record("lines-cleared")
        return data

    @router.post("/avatar/say")
    async def avatar_say(request: Request) -> dict:
        """合成**一句话**给角色窗口播放 ✗。

        和 `/api/read` 的关键区别：**不建任务、不推送** ✗ ——
        所以不会打扰跟读窗、也不会打断你正在听的内容 ✅
        （进的是**同一个缓存** ✗，同一句第二次秒回 ✅）
        """

        try:
            payload = await request.json()
            text = str((payload or {}).get("text") or "").strip()
            character = str((payload or {}).get("character") or "").strip() or None
        except Exception:  # noqa: BLE001
            raise _invalid("要传 {\"text\": \"...\"}") from None
        if not text:
            raise _invalid("text 不能为空")
        text = text[:200]

        options = SynthOptions.from_dict({})
        sentences = prepare_sentences(text, {}, runtime.settings.clean)
        if not sentences:
            raise _invalid("清洗后没有可读的内容")

        # 角色说话：**用户选过的音色 > 角色包声明的 > 后端默认** ✗
        voice = _avatar_voice(character)
        try:
            segment, cached = await synthesize_sentence(
                sentences[0],
                voice,
                options,
                runtime.tts,
                runtime.cache,
                runtime.settings.fallback_voice,
            )
        except Exception as exc:  # noqa: BLE001 - 合成失败不该让点击炸掉 ✗
            runtime.record("avatar-say-failed", text=sentences[0], error=str(exc)[:120])
            raise HTTPException(
                502, {"code": "SAY_FAILED", "message": "合成失败：{}".format(exc)}
            ) from exc

        runtime.record("avatar-say", text=sentences[0], cached=bool(cached))
        return {
            "text": sentences[0],
            "voice": voice,
            "cached": bool(cached),
            # ⚠️ 字段是 `audio_id` 不是 `id` ✗（踩过 ✗）
            "url": "/api/audio/{}".format(segment.audio_id),
        }

    # -- 角色聊天（大模型生成一句 ✗，然后 TTS）-----------------------------
    # 三个接口位都在**仓库外**的 `~/.config/nekopal/` ✗：
    #   chat.yaml（含 api_key ✗）/ profile.md（私人信息 ✗）/ characters/<id>/persona.md
    # ⚠️ api_key **绝不**进日志、**绝不**通过接口返回 ✗（见 nekopal/chat.py 的注释 ✗）
    @router.get("/chat")
    async def chat_status() -> dict:
        """LLM 配好没有（只回 has_key ✗，不回 key 本身 ✗）。"""

        from nekopal import chat as chat_mod

        return chat_mod.public_status()

    @router.post("/chat/open")
    async def chat_open() -> dict:
        """在文件管理器里打开配置文件夹 ✗。

        为什么要它：那两个文件（`chat.yaml` / `profile.md`）在 `~/.config/nekopal/` 下 ✗，
        普通人**根本找不到** ✗ —— 所以界面上给一个「一键打开」✅
        """

        import subprocess as _sp
        import sys as _sys

        from nekopal.packs import config_dir

        target = config_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        opener = "open" if _sys.platform == "darwin" else "xdg-open"
        try:
            _sp.Popen([opener, str(target)])
        except Exception as exc:  # noqa: BLE001
            raise _invalid("打不开文件夹：{}".format(exc)) from None
        return {"opened": str(target)}

    @router.put("/chat")
    async def chat_config(request: Request) -> dict:
        """改 LLM 配置（合并式 ✗）。空字符串 = 清掉该项 ✗。"""

        from nekopal import chat as chat_mod

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise _invalid("JSON 格式不对") from None
        if not isinstance(payload, dict):
            raise _invalid("要传一个对象")
        chat_mod.save_config(payload)
        return chat_mod.public_status()

    #: 正在后台补池子的角色 ✗（防止并发重复生成 ✗）
    _refilling: set = set()

    def _schedule_refill(character, have: int) -> None:
        """池子快见底时，**后台悄悄再生成一批** ✅（失败静默 ✗）。

        为什么后台 ✗：补池子要好几秒 ✗ —— 绝不能让你点一下等它 ✗。
        """

        from nekopal import chat as chat_mod

        if have > 5 or character in _refilling or not chat_mod.is_enabled():
            return
        _refilling.add(character)

        def _work() -> None:
            try:
                data = chat_mod.generate_lines(character, chat_mod.DEFAULT_BATCH)
                runtime.record("lines-refilled", count=data["count"], reason="low")
            except Exception as exc:  # noqa: BLE001 - 后台失败不打扰用户 ✗
                runtime.record("lines-refill-failed", error=str(exc)[:120])
            finally:
                _refilling.discard(character)

        import threading

        threading.Thread(target=_work, daemon=True, name="nekopal-lines").start()

    @router.post("/avatar/chat")
    async def avatar_chat(request: Request) -> dict:
        """**挑一句台词 + 合成** —— 角色窗口点一下时用这个。

        优先级（`ui.json` 的 `line_source` ✗）：
        - `pool`（默认 ✗）：从**预生成的台词池**里抽 ✅ —— **零延迟** ✅
        - `live`：每次都让模型现编 ✗ —— 更应景，但要等几秒 ✗
        池子空了 / 没配 LLM / 生成失败 → 自动退回角色包的静态台词 ✅
        """

        from nekopal import chat as chat_mod

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        character = str((payload or {}).get("character") or "").strip() or None

        enabled = chat_mod.is_enabled()
        pool = chat_mod.load_lines(character)["lines"] if enabled else []
        mode = str(_load_ui().get("line_source") or "pool")

        generated = None
        note = "static"

        # ① 预生成池（快 ✅）
        if enabled and mode == "pool" and pool:
            picked = chat_mod.pick_line(character, pool)
            if picked:
                generated = {"text": picked, "model": "(pool)"}
                note = "pool"
                _schedule_refill(character, len(pool))

        # ② 现编（慢 ✗，但应景 ✅）
        if generated is None and enabled:
            try:
                generated = await asyncio.to_thread(chat_mod.generate_line, character)
                note = "ai"
            except Exception as exc:  # noqa: BLE001 - 生成失败不是错误 ✗
                note = "static-after-{}".format(type(exc).__name__)
                runtime.record("avatar-chat-failed", error=str(exc)[:120])

        # ③ 现编失败了，但池子里还有 → 用池子兜底 ✅
        if generated is None and pool:
            picked = chat_mod.pick_line(character, pool)
            if picked:
                generated = {"text": picked, "model": "(pool)"}
                note = "pool-fallback"

        # ④ 什么都没有 → 角色包的静态台词 ✅（"不配也能用"✗）
        if generated is None:
            generated = {"text": random.choice(_voice_lines(character)), "model": "(static)"}

        text = str(generated["text"])[:200]
        options = SynthOptions.from_dict({})
        sentences = prepare_sentences(text, {}, runtime.settings.clean)
        if not sentences:
            raise _invalid("清洗后没有可读的内容")

        voice = _avatar_voice(character)
        segment, cached = await synthesize_sentence(
            sentences[0],
            voice,
            options,
            runtime.tts,
            runtime.cache,
            runtime.settings.fallback_voice,
        )
        runtime.record("avatar-chat", text=sentences[0], source=note, cached=bool(cached))
        return {
            "text": sentences[0],
            "voice": voice,
            "source": note,  # ai / static / static-after-XXX ✗（一眼看出为什么是静态 ✗）
            "cached": bool(cached),
            "url": "/api/audio/{}".format(segment.audio_id),
        }

    @router.get("/avatar-l2d/{file_path:path}")
    async def avatar_l2d(file_path: str) -> FileResponse:
        """我们自己的 Live2D 渲染外壳（esbuild 打出来的 ``pet.js`` + 着色器）。

        ⚠️ 它**不进仓库**：里面打包了 Live2D 官方的 **Cubism 5 框架**，
        与 Cubism Core 同属 Live2D 的组件，所以放在 ``~/.config/nekopal/l2d/``，
        和 Core 一样只做**本地中转**。
        """

        return _outside_file(config_dir() / "l2d", file_path, "L2D")

    @router.get("/avatar-core/{name}")
    async def avatar_core(name: str) -> FileResponse:
        """Live2D 官方的 **Cubism Core**（专有运行时，**不进仓库**）。

        由使用者自己从 Live2D 官网获取、同意条款，放在 ``~/.config/nekopal/core/``。
        这里只做**本地中转**给页面加载，项目本身不分发它。
        """

        return _outside_file(config_dir() / "core", name, "CORE")

    @router.get("/avatar/{char_id}/{file_path:path}")
    async def avatar_file(char_id: str, file_path: str) -> FileResponse:
        """把角色包里的一个文件发出去（模型 json / moc3 / 贴图 / 动作）。"""

        for pack in load_packs().characters:
            if pack.id == char_id:
                return _outside_file(Path(pack.path), file_path, "AVATAR")

        raise HTTPException(
            404, {"code": "AVATAR_NOT_FOUND", "message": "没有这个角色包"}
        )

    return router
