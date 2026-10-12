"""分句（设计文档 5.2）。

规则：
- 先按中文标点与换行切；
- 再按英文 `.!?;` + 空白切；
- 过短的句子与**后一句**合并（避免频繁起停）。
"""

from __future__ import annotations

import re
from typing import Iterable, List

# 中文/全角句末标点与换行
_SPLIT_CJK = re.compile(r"(?<=[。！？；!?;…])")
# 英文句末标点 + 空白
_SPLIT_ASCII = re.compile(r"(?<=[.!?;])\s+")
_PARAGRAPH = re.compile(r"\n+")

MIN_SENTENCE_LEN = 8


def split_sentences(text: str, min_len: int = MIN_SENTENCE_LEN) -> List[str]:
    """把一段文本切成句列表。空文本返回空列表。"""

    pieces: List[str] = []
    for paragraph in _PARAGRAPH.split(text or ""):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for chunk in _SPLIT_CJK.split(paragraph):
            chunk = chunk.strip()
            if not chunk:
                continue
            for part in _SPLIT_ASCII.split(chunk):
                part = part.strip()
                if part:
                    pieces.append(part)

    return _merge_short(pieces, min_len)


def _merge_short(pieces: Iterable[str], min_len: int) -> List[str]:
    merged: List[str] = []
    buf = ""
    for piece in pieces:
        buf = buf + piece if buf else piece
        if len(buf) >= min_len:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] += buf
        else:
            merged.append(buf)
    return merged
