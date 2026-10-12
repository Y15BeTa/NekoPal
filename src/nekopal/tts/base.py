"""TTS 后端协议。

新增一个后端只需：实现本协议 → 在 ``tts/registry.py`` 注册 → 完成。
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from nekopal.core.models import Capabilities, SynthOptions, SynthResult, Voice


@runtime_checkable
class TTSBackend(Protocol):
    id: str
    name: str

    def capabilities(self) -> Capabilities:
        ...

    def available(self) -> bool:
        """当前环境是否可用（依赖是否装上、命令是否存在）。"""
        ...

    async def list_voices(self) -> List[Voice]:
        ...

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        ...

    async def aclose(self) -> None:
        ...
