"""角色层注册表（Phase 1 只有空实现）。"""

from __future__ import annotations

from typing import Any, Dict, List

from nekopal.avatar.base import AvatarBackend, NullAvatar


class AvatarRegistry:
    def __init__(self) -> None:
        self._backends: List[AvatarBackend] = []

    def register(self, backend: AvatarBackend) -> None:
        self._backends.append(backend)

    def all(self) -> List[AvatarBackend]:
        return list(self._backends)

    def active(self) -> List[AvatarBackend]:
        return [b for b in self._backends if b.id != "null"]

    def describe(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": b.id,
                "name": b.name,
                "capabilities": b.capabilities().to_dict(),
                "assets": b.render_web_assets(),
            }
            for b in self._backends
        ]


def build_avatar_registry() -> AvatarRegistry:
    registry = AvatarRegistry()
    registry.register(NullAvatar())
    return registry
