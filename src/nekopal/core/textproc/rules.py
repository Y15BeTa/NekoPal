"""内置清洗规则（设计文档 5.1）。

顺序要点：``skip_code`` 必须早于 ``strip_markdown``，
否则 Markdown 规则会把 ```` ``` ```` 代码围栏破坏掉，导致代码块无法识别。
"""

from __future__ import annotations

import re
from typing import Dict, List

from nekopal.core.textproc.base import ProcContext

# --------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------

_ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\ufeff\u00ad]")
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t\u3000]{2,}")
_FW_TABLE: Dict[int, int] = {}


def _build_fullwidth_table() -> Dict[int, int]:
    table: Dict[int, int] = {}
    for base, ascii_base, count in ((0xFF10, 0x30, 10), (0xFF21, 0x41, 26), (0xFF41, 0x61, 26)):
        for i in range(count):
            table[base + i] = ascii_base + i
    return table


_FW_TABLE = _build_fullwidth_table()


class Normalize:
    """统一换行、去零宽字符、全角字母数字归一半角（保留中文标点）。"""

    id = "normalize"

    def process(self, text: str, ctx: ProcContext) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _ZERO_WIDTH.sub("", text)
        text = text.translate(_FW_TABLE)
        lines = [line.rstrip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = _MULTI_SPACE.sub(" ", text)
        text = _MULTI_BLANK.sub("\n\n", text)
        return text


# --------------------------------------------------------------------------
# skip_code
# --------------------------------------------------------------------------

_FENCED = re.compile(r"```.*?```", re.S)
_FENCED_TILDE = re.compile(r"~~~.*?~~~", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


class SkipCode:
    """删除代码块；行内代码保留内容（术语常写在行内代码里，读出来更有用）。"""

    id = "skip_code"

    def process(self, text: str, ctx: ProcContext) -> str:
        text = _FENCED.sub("\n", text)
        text = _FENCED_TILDE.sub("\n", text)
        text = _INLINE_CODE.sub(r"\1", text)
        return text


# --------------------------------------------------------------------------
# skip_tables
# --------------------------------------------------------------------------

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


class SkipTables:
    """丢弃 Markdown 表格行（读表格很吵）。"""

    id = "skip_tables"

    def process(self, text: str, ctx: ProcContext) -> str:
        lines = [ln for ln in text.split("\n") if not _TABLE_ROW.match(ln)]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# strip_markdown
# --------------------------------------------------------------------------

_MD_SUBS = [
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),          # 标题
    (re.compile(r"^\s{0,3}>\s?", re.M), ""),                # 引用
    (re.compile(r"^\s*([-*+]|\d+\.)\s+", re.M), ""),        # 列表
    (re.compile(r"^\s*([-*_]\s*){3,}$", re.M), ""),         # 分隔线
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),         # 图片
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),          # 链接
    (re.compile(r"\*\*\*([^*]+)\*\*\*"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), r"\1"),
    (re.compile(r"(?<!_)_([^_\n]+)_(?!_)"), r"\1"),
    (re.compile(r"~~([^~]+)~~"), r"\1"),
    (re.compile(r"</?[a-zA-Z][^>]*>"), ""),                 # 简单 HTML 标签
]


class StripMarkdown:
    id = "strip_markdown"

    def process(self, text: str, ctx: ProcContext) -> str:
        for pattern, repl in _MD_SUBS:
            text = pattern.sub(repl, text)
        return text


# --------------------------------------------------------------------------
# speak_symbols
# --------------------------------------------------------------------------

_SYMBOLS: List[tuple] = [
    ("→", "箭头"),
    ("←", "左箭头"),
    ("⇒", "推出"),
    ("≥", "大于等于"),
    ("≤", "小于等于"),
    ("≠", "不等于"),
    ("≈", "约等于"),
    ("∑", "求和"),
    ("∏", "求积"),
    ("√", "根号"),
    ("∞", "无穷"),
    ("°", "度"),
    ("×", "乘"),
    ("÷", "除以"),
    ("±", "正负"),
    ("∈", "属于"),
    ("π", "派"),
    ("μ", "微"),
    ("Δ", "德尔塔"),
    ("—", "，"),
    ("–", "，"),
]


class SpeakSymbols:
    """把排版符号读成人话。"""

    id = "speak_symbols"

    def process(self, text: str, ctx: ProcContext) -> str:
        for symbol, spoken in _SYMBOLS:
            text = text.replace(symbol, spoken)
        return text


# --------------------------------------------------------------------------
# strip_emoji
# --------------------------------------------------------------------------

_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U0001F1E6-\U0001F1FF"   # 区域指示符
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\u2b00-\u2bff"
    "\ufe0f"
    "\u20e3"
    "]+"
)


class StripEmoji:
    id = "strip_emoji"

    def process(self, text: str, ctx: ProcContext) -> str:
        return _EMOJI.sub("", text)


RULES = {
    cls.id: cls()
    for cls in (
        Normalize,
        SkipCode,
        SkipTables,
        StripMarkdown,
        SpeakSymbols,
        StripEmoji,
    )
}

#: 默认执行顺序（见设计文档 5.1 的表格）
DEFAULT_ORDER: List[str] = [
    "normalize",
    "skip_code",
    "skip_tables",
    "strip_markdown",
    "speak_symbols",
    "strip_emoji",
]
