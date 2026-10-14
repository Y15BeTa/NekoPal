"""本地文档取正文（`nekopal.filetext`）—— R20「打开文件 → 逐字朗读」。

为什么值得测：这段是**手写**的 zip + XML 解析（为了零依赖）。
正则写错不会崩，只会**悄悄少读几段** —— 那种问题用户要听半天才发现。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from nekopal import filetext


def make_docx(*paragraphs: str) -> bytes:
    body = "".join(
        "<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(p) for p in paragraphs
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
            + body
            + "</w:body></w:document>",
        )
    return buf.getvalue()


def test_extracts_paragraphs():
    text, fmt = filetext.extract("讲义.docx", make_docx("第一段", "第二段"))
    assert fmt == "docx"
    assert text.splitlines() == ["第一段", "第二段"]


def test_unescapes_entities():
    text, _ = filetext.extract("x.docx", make_docx("A &amp; B &lt;C&gt;"))
    assert text == "A & B <C>"


def test_keeps_table_cells_apart():
    """表格单元格不能连成一坨（不然会读成"表格A表格B"）。"""

    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        "<w:tbl><w:tr>"
        "<w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>乙</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    text, _ = filetext.extract("x.docx", buf.getvalue())
    assert "甲" in text and "乙" in text
    assert "甲乙" not in text and "甲\n乙" not in text  # 中间要有分隔


def test_br_and_tab_become_whitespace():
    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        "<w:p><w:r><w:t>上</w:t><w:br/><w:t>下</w:t><w:tab/><w:t>右</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    text, _ = filetext.extract("x.docx", buf.getvalue())
    assert "上" in text and "下" in text and "右" in text


def test_collapses_blank_lines():
    text, _ = filetext.extract("x.docx", make_docx("甲", "", "", "", "乙"))
    assert "\n\n\n" not in text


def test_truncates_to_max():
    long = "字" * (filetext.MAX_CHARS + 5000)
    text, _ = filetext.extract("x.docx", make_docx(long))
    assert len(text) == filetext.MAX_CHARS


# ── 失败路径：**必须说清原因**，不能假装成功 ─────────────────────────


def test_bad_zip_says_so():
    with pytest.raises(ValueError, match="不是有效的 .docx"):
        filetext.extract("x.docx", b"this is not a zip")


def test_docx_without_document_xml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/other.xml", "<x/>")
    with pytest.raises(ValueError, match="document.xml"):
        filetext.extract("x.docx", buf.getvalue())


def test_empty_docx_says_so():
    with pytest.raises(ValueError, match="没抽出正文"):
        filetext.extract("x.docx", make_docx())


def test_pdf_points_at_the_missing_dependency():
    with pytest.raises(ValueError, match="pypdf"):
        filetext.extract("x.pdf", b"%PDF-1.4")


def test_old_doc_tells_you_to_resave():
    with pytest.raises(ValueError, match="另存为"):
        filetext.extract("x.doc", b"\xd0\xcf\x11\xe0")


def test_unknown_suffix_lists_what_works():
    with pytest.raises(ValueError, match="不认识的格式"):
        filetext.extract("x.xyz", b"z")
