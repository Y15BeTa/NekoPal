"""离线测试用的假后端（不在测试里调用真实 TTS）。"""

from __future__ import annotations

from typing import List

from nekopal.core.models import SynthOptions, SynthResult


class SilentBackend:
    """返回固定时长静音音频；记录调用次数以便断言缓存命中。"""

    id = "fake"
    name = "Fake"

    def __init__(self) -> None:
        self.calls: List[str] = []

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        self.calls.append(text)
        return SynthResult(
            audio=b"RIFF" + b"\x00" * 60,
            sample_rate=22050,
            duration=0.5,
            words=[],
            media_type="audio/wav",
            extension="wav",
        )


class FailingBackend:
    """对指定文本抛错，用于验证「单句失败不中断整个任务」。"""

    id = "failing"
    name = "Failing"

    def __init__(self, fail_texts: List[str]) -> None:
        self.fail_texts = fail_texts
        self.calls: List[str] = []

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        self.calls.append(text)
        if text in self.fail_texts:
            raise RuntimeError("boom")
        return SynthResult(
            audio=b"RIFF" + b"\x00" * 60,
            sample_rate=22050,
            duration=0.5,
            words=[],
            media_type="audio/wav",
            extension="wav",
        )
