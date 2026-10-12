"""核心领域模型。

设计文档 6.1 / 6.5：这些类型是跨层契约的一部分，
任何新增能力都应扩展这里，而不是在调用处写特例。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: 音色/角色的授权等级
LICENSE_LEVELS = ("official", "self-recorded", "licensed", "public-domain", "unofficial")

#: 缓存格式版本。
#: 当**合成结果的结构**发生变化（新增/修改字段、时间戳语义变化、后端行为修正）时
#: 必须递增，否则老缓存会以"看起来正常"的姿态污染新逻辑。
#:
#: v2（2026-10-13）：变声后端开始**透传 base 音色的词级时间戳**，
#: 并且采样率改为从 WAV 头读取（原来写死 24000，实测真实 RVC 输出是 48000）。
#: v1 存下来的 `words` 恒为空 → 不升版本的话，词级高亮"改了也看不出效果"。
CACHE_VERSION = "2"


@dataclass(frozen=True)
class Capabilities:
    """后端 / 角色声明自己支持什么（设计文档 6.7）。"""

    word_timings: bool = False
    rate: bool = False
    pitch: bool = False
    volume: bool = False
    streaming: bool = False
    offline: bool = True

    def to_dict(self) -> Dict[str, bool]:
        return {
            "word_timings": self.word_timings,
            "rate": self.rate,
            "pitch": self.pitch,
            "volume": self.volume,
            "streaming": self.streaming,
            "offline": self.offline,
        }


@dataclass
class WordTiming:
    """音频时间轴上的一个词/字区间（秒）。"""

    t0: float
    t1: float
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"t0": round(self.t0, 3), "t1": round(self.t1, 3), "text": self.text}


@dataclass
class SynthOptions:
    """影响**音频字节**的合成参数。

    注意：``playbackRate``（前端语速）**不在这里**，它不影响音频字节，
    因此不进缓存键（设计文档 5.7 / 6.4）。
    """

    rate: float = 1.0
    pitch: float = 0.0
    volume: float = 1.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SynthOptions":
        data = data or {}
        return cls(
            rate=float(data.get("rate", 1.0)),
            pitch=float(data.get("pitch", 0.0)),
            volume=float(data.get("volume", 1.0)),
        )

    def cache_part(self) -> Dict[str, float]:
        return {
            "rate": round(self.rate, 4),
            "pitch": round(self.pitch, 4),
            "volume": round(self.volume, 4),
        }


@dataclass
class SynthResult:
    audio: bytes
    sample_rate: int
    duration: float
    words: List[WordTiming] = field(default_factory=list)
    media_type: str = "audio/wav"
    extension: str = "wav"

    def words_dict(self) -> List[Dict[str, Any]]:
        return [w.to_dict() for w in self.words]


@dataclass
class Voice:
    id: str
    name: str
    backend: str
    lang: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    capabilities: Capabilities = field(default_factory=Capabilities)
    license: str = "official"
    commercial_ok: bool = True
    redistributable: bool = True
    preview: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "backend": self.backend,
            "lang": list(self.lang),
            "tags": list(self.tags),
            "capabilities": self.capabilities.to_dict(),
            "license": self.license,
            "commercial_ok": self.commercial_ok,
            "redistributable": self.redistributable,
            "preview": self.preview,
        }


@dataclass
class ReadRequest:
    """一次朗读请求（设计文档 4.2 ``POST /api/read``）。"""

    text: str
    voice: str
    source: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    client_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], default_voice: str) -> "ReadRequest":
        return cls(
            text=str(data.get("text") or ""),
            voice=str(data.get("voice") or default_voice),
            source=dict(data.get("source") or {}),
            options=dict(data.get("options") or {}),
            client_id=data.get("client_id"),
        )


def cache_key(text: str, voice_id: str, options: SynthOptions) -> str:
    """内容寻址键（设计文档 5.7）。

    只有**影响音频字节**的参数参与哈希；``playbackRate``、``start_index``
    等播放侧参数一律不参与。
    """

    payload = "\x00".join(
        [
            CACHE_VERSION,
            text,
            voice_id,
            json.dumps(options.cache_part(), sort_keys=True, ensure_ascii=False),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
