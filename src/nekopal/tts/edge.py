"""Edge-TTS 后端（音色丰富 + 词级时间戳；需联网，非官方接口）。

- 提供 ``WordBoundary`` 词级时间戳 → 前端可做逐词高亮
- 文本会发送至微软服务器（README 与设置界面需明示）
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any, Dict, List, Tuple

from nekopal.core.models import (
    Capabilities,
    SynthOptions,
    SynthResult,
    Voice,
    WordTiming,
)
from nekopal.media import MP3_BYTES_PER_SECOND

#: 网络不可用时的最小音色集（保证 UI 不会空着）
FALLBACK_VOICES: List[Tuple[str, str, str, List[str]]] = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（普通话·女）", "zh", ["female"]),
    ("zh-CN-XiaoyiNeural", "晓伊（普通话·女）", "zh", ["female"]),
    ("zh-CN-YunxiNeural", "云希（普通话·男）", "zh", ["male"]),
    ("zh-CN-YunjianNeural", "云健（普通话·男）", "zh", ["male"]),
    ("zh-CN-YunyangNeural", "云扬（普通话·男·新闻）", "zh", ["male", "news"]),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北（东北官话·女）", "zh", ["female", "dialect"]),
    ("zh-TW-HsiaoChenNeural", "曉臻（台湾国语·女）", "zh", ["female"]),
    ("ja-JP-NanamiNeural", "七海（日语·女）", "ja", ["female"]),
    ("ja-JP-KeiNeural", "圭（日语·男）", "ja", ["male"]),
    ("en-US-AriaNeural", "Aria（美式英语·女）", "en", ["female"]),
    ("en-US-GuyNeural", "Guy（美式英语·男）", "en", ["male"]),
    ("en-GB-SoniaNeural", "Sonia（英式英语·女）", "en", ["female"]),
]


class EdgeBackend:
    id = "edge"
    name = "Edge-TTS"

    def __init__(self) -> None:
        self._voices: List[Voice] | None = None

    # -- 能力 ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            word_timings=True,
            rate=True,
            pitch=True,
            volume=True,
            streaming=True,
            offline=False,
        )

    def available(self) -> bool:
        return importlib.util.find_spec("edge_tts") is not None

    # -- 音色 ------------------------------------------------------------
    def _make_voice(self, short_name: str, display: str, lang: str, tags: List[str]) -> Voice:
        return Voice(
            id="edge:{}".format(short_name),
            name=display,
            backend=self.id,
            lang=[lang],
            tags=tags,
            capabilities=self.capabilities(),
            license="official",
            commercial_ok=True,
            redistributable=False,  # 音色归服务方，不随本项目分发
        )

    async def list_voices(self) -> List[Voice]:
        if self._voices is not None:
            return list(self._voices)

        raw: List[Dict[str, Any]] | None = None
        if self.available():
            try:
                import edge_tts

                raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=8)
            except Exception:  # noqa: BLE001 - 离线/接口变更时静默降级
                raw = None

        voices: List[Voice] = []
        if raw:
            for item in raw:
                short = item.get("ShortName")
                if not short:
                    continue
                status = item.get("Status")
                if status and status != "GA":
                    continue
                locale = item.get("Locale") or ""
                gender = (item.get("Gender") or "").lower()
                voices.append(
                    self._make_voice(
                        short,
                        "{}（{}·{}）".format(
                            item.get("FriendlyName") or short,
                            locale,
                            {"female": "女", "male": "男"}.get(gender, gender or "?"),
                        ),
                        locale.split("-")[0] if locale else "und",
                        [gender] if gender else [],
                    )
                )

        if voices:
            self._voices = voices  # 只缓存**联网枚举成功**的结果
            return list(voices)

        # 离线（或接口变更）：给一份兜底列表，但**不写进缓存** ——
        # 否则服务启动那一刻正好断网，就会永远只看到这十几个音色，直到重启服务。
        return [self._make_voice(*entry) for entry in FALLBACK_VOICES]

    # -- 合成 ------------------------------------------------------------
    @staticmethod
    def _make_communicate(edge_tts, text: str, voice: str, kwargs: Dict[str, str]):
        """构造 Communicate，并显式要求**词级**时间戳。

        设计文档 5.3 依赖词级时间戳做逐词高亮；edge-tts 默认给的是
        ``SentenceBoundary``，必须显式切到 ``WordBoundary``。
        老版本没有该参数，则退回默认（前端会自动降级为句级高亮）。
        """

        try:
            return edge_tts.Communicate(text, voice, boundary="WordBoundary", **kwargs)
        except TypeError:
            return edge_tts.Communicate(text, voice, **kwargs)

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        if not self.available():
            raise RuntimeError("edge-tts 未安装：请执行 `uv pip install edge-tts`")

        import edge_tts

        local = voice_id.split(":", 1)[1] if ":" in voice_id else voice_id

        kwargs: Dict[str, str] = {}
        if abs(options.rate - 1.0) > 1e-3:
            kwargs["rate"] = "{:+d}%".format(int(round((options.rate - 1.0) * 100)))
        if abs(options.volume - 1.0) > 1e-3:
            kwargs["volume"] = "{:+d}%".format(int(round((options.volume - 1.0) * 100)))
        if abs(options.pitch) > 1e-3:
            # 半音 → Hz 为近似换算，仅作方向性调节
            kwargs["pitch"] = "{:+d}Hz".format(int(round(options.pitch * 50)))

        communicate = self._make_communicate(edge_tts, text, local, kwargs)
        chunks = bytearray()
        words: List[WordTiming] = []

        async for chunk in communicate.stream():
            kind = chunk.get("type")
            if kind == "audio":
                chunks.extend(chunk.get("data") or b"")
            elif kind == "WordBoundary":
                offset = float(chunk.get("offset", 0)) / 1e7
                duration = float(chunk.get("duration", 0)) / 1e7
                words.append(
                    WordTiming(t0=offset, t1=offset + duration, text=str(chunk.get("text", "")))
                )

        audio = bytes(chunks)
        if not audio:
            raise RuntimeError("edge-tts 没有返回音频（网络问题？）")

        # 时长取「最后一个词的结束」与「按字节估算」的较大值 ——
        # 只取前者会漏掉尾部的静音（实测差 0.5 秒以上）。
        by_bytes = len(audio) / MP3_BYTES_PER_SECOND
        duration = max(words[-1].t1 if words else 0.0, by_bytes)
        return SynthResult(
            audio=audio,
            sample_rate=24000,
            duration=duration,
            words=words,
            media_type="audio/mpeg",
            extension="mp3",
        )

    async def aclose(self) -> None:
        return None
