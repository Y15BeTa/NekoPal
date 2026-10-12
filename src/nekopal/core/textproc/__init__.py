"""文本清洗管线（设计文档 5.1）。"""

from nekopal.core.textproc.chain import DEFAULT_ORDER, apply_clean
from nekopal.core.textproc.base import ProcContext, TextProcessor

__all__ = ["DEFAULT_ORDER", "apply_clean", "ProcContext", "TextProcessor"]
