"""清洗规则插件协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol, runtime_checkable


@dataclass
class ProcContext:
    """清洗上下文（当前只记录启用了哪些规则）。"""

    enabled: List[str] = field(default_factory=list)


@runtime_checkable
class TextProcessor(Protocol):
    """一条清洗规则。新增规则只需实现本协议并注册（设计文档 6.3）。"""

    id: str

    def process(self, text: str, ctx: ProcContext) -> str:  # pragma: no cover - 协议
        ...
