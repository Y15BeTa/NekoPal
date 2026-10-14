"""从**本地文档**里抽正文（给「打开文件 → 逐字朗读」用，`R20`）。

## 为什么是零依赖的

项目一直在刻意控制依赖 ✗。而「把一份 docx 的文字读出来」这件事，
用标准库就够了 ✅：

- `.docx` 本质是个 **zip** ✗ → `zipfile` 打开 → 读 `word/document.xml` ✗ → 把标签换成换行 / 去标签 ✅
- `.md` / `.txt` 就是纯文本 ✗（前端直接读，不走这里 ✗）

**没装 `python-docx` 也能用** —— 只需要正文文字，不需要样式 / 表格结构 ✅

## 支持与不支持

| 格式 | 行不行 | 怎么做 |
| --- | --- | --- |
| `.txt` / `.md` / `.markdown` | ✅ | **前端**直接读（不用传上来 ✗）|
| `.docx` | ✅ | 这里解（zip + XML ✗）|
| `.pdf` | ❌ | 需要 `pypdf` / `pymupdf` —— **没装就明确告诉用户**，不要假装成功 ✗ |
| `.doc`（老的二进制格式）| ❌ | 那是完全不同的格式 ✗ —— 提示用户另存为 `.docx` |

⚠️ **不假装成功** ✗：抽不出东西就抛错并说清原因 ✗，
否则用户会对着一个空输入框发呆 ✗。
"""

from __future__ import annotations

import html
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Tuple

__all__ = ["CLIENT_SIDE", "MAX_CHARS", "SERVER_SIDE", "SUPPORTED_NAMES", "extract"]

#: 抽出来的正文上限（再长也没人听得完 ✗，而且会拖垮分句 ✗）
MAX_CHARS = 200_000

#: 前端直接读、不用传上来的
CLIENT_SIDE = {".txt", ".md", ".markdown", ".text", ""}

#: 这里能解的
SERVER_SIDE = {".docx", ".docm"}

SUPPORTED_NAMES = "md / txt（直接读）· docx"


def _strip_xml(xml: str) -> str:
    """把 `word/document.xml` 变成人读的文本。

    只做**结构到换行**的映射 ✗，不解析样式 ✗：
    段落 / 换行 / 制表符 → 相应的空白 ✗，其余标签一律丢掉 ✅
    """

    # 段落结束 → 换行；<w:br/> 也换行；<w:tab/> → 制表符
    text = re.sub(r"</w:p\s*>", "\n", xml)
    text = re.sub(r"<w:br\s*/?>", "\n", text)
    text = re.sub(r"<w:tab\s*/?>", "\t", text)
    # 表格单元格之间给个分隔（不然会连成一坨 ✗）
    text = re.sub(r"</w:tc\s*>", "\t", text)
    text = re.sub(r"</w:tr\s*>", "\n", text)
    # 其余标签全丢
    text = re.sub(r"<[^>]+>", "", text)
    # 实体还原
    text = html.unescape(text)
    # 收拾空白：行尾空白去掉、连续空行最多留一个
    lines = [ln.rstrip() for ln in text.splitlines()]
    out = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def extract(name: str, data: bytes) -> Tuple[str, str]:
    """从字节里抽正文 ✗。返回 ``(文本, 格式)``；抽不出就抛 ``ValueError``（带原因 ✗）。"""

    suffix = Path(name or "").suffix.lower()

    if suffix in SERVER_SIDE:
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                # ⚠️ 主文档在 word/document.xml ✗；没有就说明这不是个 docx ✗
                if "word/document.xml" not in zf.namelist():
                    raise ValueError("这个文件里没有 word/document.xml，可能不是 .docx")
                xml = zf.read("word/document.xml").decode("utf-8", "ignore")
        except zipfile.BadZipFile:
            raise ValueError(
                "这个文件不是有效的 .docx（是不是把 .doc 直接改了后缀？另存为 .docx 再试）"
            ) from None
        text = _strip_xml(xml)
        if not text:
            raise ValueError("这个 .docx 里没抽出正文（可能是空的 / 全是图片）")
        return text[:MAX_CHARS], "docx"

    if suffix == ".pdf":
        raise ValueError(
            "PDF 还没支持 —— 需要装 pypdf 或 pymupdf。"
            "（也可以先另存为 docx 或 txt）"
        )

    if suffix == ".doc":
        raise ValueError("老的 .doc（二进制格式）不支持 —— 请另存为 .docx")

    raise ValueError("不认识的格式：{}（支持 {}）".format(suffix or "（无后缀）", SUPPORTED_NAMES))
