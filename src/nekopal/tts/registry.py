"""TTS 后端注册表。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from nekopal.core.models import SynthOptions, SynthResult, Voice
from nekopal.tts.base import TTSBackend
from nekopal.tts.edge import EdgeBackend
from nekopal.tts.macos_say import SayBackend


class TTSRegistry:
    def __init__(self) -> None:
        self._backends: Dict[str, TTSBackend] = {}
        self._order: List[str] = []

    # -- 注册 ------------------------------------------------------------
    def register(self, backend: TTSBackend) -> None:
        if backend.id in self._backends:
            return
        self._backends[backend.id] = backend
        self._order.append(backend.id)

    def get(self, backend_id: str) -> Optional[TTSBackend]:
        return self._backends.get(backend_id)

    def all(self) -> List[TTSBackend]:
        return [self._backends[bid] for bid in self._order]

    def default(self) -> Optional[TTSBackend]:
        for backend in self.all():
            if backend.available():
                return backend
        return self.all()[0] if self._order else None

    # -- 解析 ------------------------------------------------------------
    @staticmethod
    def split(voice_id: str) -> Tuple[str, str]:
        if ":" in voice_id:
            head, tail = voice_id.split(":", 1)
            return head, tail
        return "", voice_id

    def resolve(self, voice_id: str) -> Tuple[Optional[TTSBackend], str]:
        backend_id, local = self.split(voice_id)
        if backend_id:
            return self._backends.get(backend_id), local
        return self.default(), local

    def resolve_voice_id(self, voice_id: str, fallback: Optional[str] = None) -> str:
        """把 ``say:auto`` 之类的占位符落成具体音色，保证缓存键稳定。

        若该音色所属后端**不可用**（依赖没装、命令不存在），则改用 ``fallback``。
        这样默认音色可以放心地用联网的 Edge-TTS，离线时自动退回系统 ``say``。
        """

        backend, local = self.resolve(voice_id)
        if backend is None or not backend.available():
            if fallback and fallback != voice_id:
                return self.resolve_voice_id(fallback)
            return voice_id
        if local in ("", "auto") and hasattr(backend, "default_voice_id"):
            return backend.default_voice_id()
        return voice_id

    # -- 查询 ------------------------------------------------------------
    def stamp_capabilities(self, voices: List[Voice]) -> List[Voice]:
        """让音色继承它所属**后端**的能力。

        后端是能力的**唯一来源**（设计文档 6.7）：音色包只声明"用哪个后端"，
        不该自己再编一份能力出来 —— 否则界面的能力协商会读到假信息
        （踩过：包音色一度报 `rate=False`，而 rvc 后端其实是 True）。
        """

        for voice in voices:
            backend = self.get(voice.backend)
            if backend is not None:
                voice.capabilities = backend.capabilities()
        return voices

    def describe(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for backend in self.all():
            caps = backend.capabilities()
            out.append(
                {
                    "id": backend.id,
                    "name": backend.name,
                    "available": backend.available(),
                    "offline": caps.offline,
                    "capabilities": caps.to_dict(),
                }
            )
        return out

    async def voices(self) -> List[Voice]:
        results = await asyncio.gather(
            *(backend.list_voices() for backend in self.all()),
            return_exceptions=True,
        )
        out: List[Voice] = []
        for result in results:
            if isinstance(result, list):
                out.extend(result)
        return out

    # -- 合成 ------------------------------------------------------------
    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        backend, _ = self.resolve(voice_id)
        if backend is None:
            raise RuntimeError("未知的后端: {}".format(voice_id))
        if not backend.available():
            raise RuntimeError("后端当前不可用: {}".format(backend.id))
        return await backend.synth(voice_id, text, options)

    async def aclose(self) -> None:
        for backend in self.all():
            try:
                await backend.aclose()
            except Exception:  # noqa: BLE001
                pass


def build_registry(settings=None) -> TTSRegistry:
    """内置后端。

    ``say`` / ``edge`` 是 ``official`` 授权等级，符合 G10「默认即合法」（设计文档 4.7）；
    ``rvc`` 是**变声**后端 —— 它自己不带音色，只服务用户自备的变声模型；
    ``sovits`` 是**端到端**后端（文字 → 目标音色）—— 同理，只服务用户自备的权重。
    """

    from nekopal.config import load_settings
    from nekopal.tts.rvc import RvcBackend
    from nekopal.tts.sovits import SovitsBackend

    settings = settings or load_settings()
    registry = TTSRegistry()
    registry.register(SayBackend())
    registry.register(EdgeBackend())
    registry.register(RvcBackend(settings))
    registry.register(SovitsBackend(settings))
    return registry
