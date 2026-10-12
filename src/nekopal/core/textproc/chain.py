"""清洗链的执行入口。"""

from __future__ import annotations

from typing import Iterable, List, Optional

from nekopal.core.textproc.base import ProcContext
from nekopal.core.textproc.rules import DEFAULT_ORDER, RULES

__all__ = ["DEFAULT_ORDER", "apply_clean", "available_rules"]


def available_rules() -> List[str]:
    return list(DEFAULT_ORDER)


def apply_clean(text: str, enabled: Optional[Iterable[str]] = None) -> str:
    """按 ``enabled`` 顺序执行清洗规则；``enabled=None`` 表示全部启用。"""

    use = list(DEFAULT_ORDER) if enabled is None else [r for r in enabled]
    ctx = ProcContext(enabled=use)
    out = text or ""
    for rule_id in DEFAULT_ORDER:
        if rule_id not in use:
            continue
        rule = RULES.get(rule_id)
        if rule is not None:
            out = rule.process(out, ctx)
    return out.strip()
