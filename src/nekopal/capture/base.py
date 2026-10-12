"""抓取源协议。

注意 ``granularity``：本项目**主路径是 ``block``（整块）**，
选区（``selection``）只是其中一种来源（设计文档 6.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Protocol, runtime_checkable


@dataclass(frozen=True)
class CaptureInfo:
    id: str
    name: str
    platform: str
    granularity: str  # "block" | "selection"
    status: str  # "active" | "planned"
    description: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "granularity": self.granularity,
            "status": self.status,
            "description": self.description,
        }


@runtime_checkable
class CaptureSource(Protocol):
    id: str
    name: str
    platform: str
    granularity: str

    def info(self) -> CaptureInfo:
        ...

    def start(self, emit: Callable[[dict], None]) -> None:
        ...

    def stop(self) -> None:
        ...
