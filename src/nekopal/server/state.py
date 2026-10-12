"""运行时状态容器：把 settings / 各注册表 / 缓存放一起。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional, Set

from nekopal.avatar.registry import AvatarRegistry, build_avatar_registry
from nekopal.capture.registry import CaptureRegistry, build_capture_registry
from nekopal.config import Settings, load_settings
from nekopal.core.cache import AudioCache
from nekopal.server.tasks import TaskRegistry
from nekopal.tts.registry import TTSRegistry, build_registry


@dataclass
class Runtime:
    settings: Settings
    tts: TTSRegistry
    cache: AudioCache
    tasks: TaskRegistry = field(default_factory=TaskRegistry)
    captures: CaptureRegistry = field(default_factory=build_capture_registry)
    avatars: AvatarRegistry = field(default_factory=build_avatar_registry)
    #: 最近的活动记录（推送来源、插件事件、错误……）——排查集成问题时看它
    activity: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))
    #: 全服事件订阅者（跟读窗用它感知「外部推送了新任务」）
    _subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = field(default_factory=set)
    #: 去重表：fingerprint -> (task_id, 时间戳)
    _recent_reads: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(cls, settings: Optional[Settings] = None) -> "Runtime":
        settings = settings or load_settings()
        return cls(
            settings=settings,
            tts=build_registry(),
            cache=AudioCache(settings.cache_dir, max_mb=settings.cache_max_mb),
        )

    def record(self, event: str, **fields: Any) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"at": time.time(), "event": event}
        entry.update(fields)
        self.activity.appendleft(entry)
        return entry

    # -- 广播（跟读窗靠它知道「有人从外部推了一整段文字」）-------------
    def subscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        self._subscribers.add(queue)

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        self._subscribers.discard(queue)

    def notify(self, event: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {"event": event, "at": time.time()}
        payload.update(fields)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # 掉帧好过阻塞主流程

    # -- 重复推送去重 ----------------------------------------------------
    def check_duplicate(self, fingerprint: str, window: float) -> Optional[str]:
        """最近 ``window`` 秒内推过同样的内容，返回原来的 task_id。"""

        now = time.time()
        previous = self._recent_reads.get(fingerprint)
        if previous is not None and now - previous[1] < window:
            return previous[0]
        self._prune_recent(now, window)
        return None

    def remember_read(self, fingerprint: str, task_id: str) -> None:
        self._recent_reads[fingerprint] = (task_id, time.time())

    def _prune_recent(self, now: float, window: float) -> None:
        stale = [key for key, (_, at) in self._recent_reads.items() if now - at > window * 6]
        for key in stale:
            self._recent_reads.pop(key, None)


    async def warmup(self) -> None:
        """启动时预热（设计文档 R5）：把 ``say`` 音色枚举跑掉，避免首次请求卡顿。"""

        say = self.tts.get("say")
        preload = getattr(say, "preload", None)
        if preload is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, preload)
        except Exception:  # noqa: BLE001 - 预热失败不影响服务
            pass

    async def aclose(self) -> None:
        self.tasks.cancel_all()
        await self.tts.aclose()
