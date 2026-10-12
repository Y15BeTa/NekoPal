"""角色层（Avatar）—— 本轮只放接口与空实现。

设计文档 6.5：角色层是**可选观察者**，它不可用时朗读链路必须照常工作。
Phase 3 才会有真正的 Live2D 实现。
"""

from nekopal.avatar.base import AvatarBackend, AvatarCapabilities, PlaybackState
from nekopal.avatar.registry import AvatarRegistry, build_avatar_registry

__all__ = [
    "AvatarBackend",
    "AvatarCapabilities",
    "PlaybackState",
    "AvatarRegistry",
    "build_avatar_registry",
]
