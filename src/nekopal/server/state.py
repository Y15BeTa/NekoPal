"""运行时状态容器：把 settings / 各注册表 / 缓存放一起。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

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

    @classmethod
    def build(cls, settings: Optional[Settings] = None) -> "Runtime":
        settings = settings or load_settings()
        return cls(
            settings=settings,
            tts=build_registry(),
            cache=AudioCache(settings.cache_dir, max_mb=settings.cache_max_mb),
        )

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
