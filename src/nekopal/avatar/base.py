"""角色层协议（设计文档 6.5 / 6.6）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class AvatarCapabilities:
    lipsync: bool = False
    expressions: bool = False
    window_mode: bool = False  # 桌宠：透明背景 / 置顶 / 鼠标穿透

    def to_dict(self) -> Dict[str, bool]:
        return {
            "lipsync": self.lipsync,
            "expressions": self.expressions,
            "window_mode": self.window_mode,
        }


@dataclass
class PlaybackState:
    """跟读窗 → 角色层的最小状态同步（设计文档 6.6）。"""

    task_id: Optional[str] = None
    segment_index: int = 0
    word_index: Optional[int] = None
    position: float = 0.0
    playing: bool = False
    emotion: str = "idle"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "segment_index": self.segment_index,
            "word_index": self.word_index,
            "position": round(self.position, 3),
            "playing": self.playing,
            "emotion": self.emotion,
        }


@runtime_checkable
class AvatarBackend(Protocol):
    id: str
    name: str

    def capabilities(self) -> AvatarCapabilities:
        ...

    def render_web_assets(self) -> List[str]:
        ...

    async def on_state(self, state: PlaybackState) -> None:
        ...


class NullAvatar:
    """空实现：Phase 1 用它占位，保证 ``/avatar/*`` 契约存在但不做事。"""

    id = "null"
    name = "（未启用角色层）"

    def capabilities(self) -> AvatarCapabilities:
        return AvatarCapabilities()

    def render_web_assets(self) -> List[str]:
        return []

    async def on_state(self, state: PlaybackState) -> None:
        return None
