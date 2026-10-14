"""前端静态资源的一致性检查。

为什么要它：2026-10-14 重排设置页时，我按「区块」替换 CSS，
**顺手把 `.switch`（开关）和整个「预生成台词池」的样式一起删掉了** ✗ ——
页面上表面还能跑，只是开关变回裸复选框、说明挤成一行、台词池没边框 ✗。
这种"能跑但变丑"的回归，**测试不跑就发现不了** ✗。

所以钉一条最简单的：**html 里用到的每个 class，css 里必须能找到** ✅。
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def _classes_in_html() -> set:
    found = set()
    for f in WEB.glob("*.html"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'class="([^"]+)"', text):
            found.update(m.group(1).split())
    return found


def _selectors_in_css() -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in WEB.glob("*.css"))


def test_every_html_class_has_css():
    html_classes = _classes_in_html()
    css = _selectors_in_css()
    missing = sorted(c for c in html_classes if ("." + c) not in css)
    assert not missing, (
        "这些 class 在 HTML 里用了，但 CSS 里找不到（多半是被误删了）：{}".format(missing)
    )


def test_every_script_is_referenced():
    """html 引的 js / js 引的 js 都真实存在（防拼错文件名）。"""

    for f in WEB.glob("*.html"):
        for m in re.finditer(r'src="([^"]+\.js)"', f.read_text(encoding="utf-8")):
            name = m.group(1)
            if name.startswith("/api/") or name.startswith("http"):
                continue  # 这些是**后端/远端**给的，不是 web/ 下的文件
            rel = name.lstrip("/")
            assert (WEB / rel).is_file(), "{} 引了不存在的 {}".format(f.name, rel)


def test_ids_referenced_by_js_exist_in_html():
    """js 里 `getElementById('x')` 的 x，html 里必须有（防改名漏改）。

    ⚠️ 只查**硬编码的字面量** —— 动态拼出来的查不了 ✗。
    """

    html_ids = set()
    for f in WEB.glob("*.html"):
        html_ids.update(re.findall(r'id="([^"]+)"', f.read_text(encoding="utf-8")))

    missing = {}
    for f in WEB.glob("*.js"):
        text = f.read_text(encoding="utf-8")
        want = set(re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", text))
        want |= set(re.findall(r"\$\(\s*'([^']+)'\s*\)", text))
        gone = sorted(x for x in want if x not in html_ids)
        if gone:
            missing[f.name] = gone
    assert not missing, "js 要的 id 在 html 里没有：{}".format(missing)


def test_clean_rules_in_ui_match_backend():
    """界面的「读前清洗」勾选项，必须和后端真正有的规则**一一对应**。

    ⚠️ 为什么值得钉：两边是**各写各的** —— 后端加一条新规则、
    界面忘了加，用户就会"少一个开关"，而且**看起来一切正常** ✗。
    """

    from nekopal.core.textproc.rules import RULES

    js = (WEB / "app.js").read_text(encoding="utf-8")
    block = js[js.index("const CLEAN_RULES = ["):]
    block = block[: block.index("];")]
    ui = set(re.findall(r"\['([a-z_]+)',", block))

    assert ui == set(RULES), "界面有 {}，后端有 {}（差：{}）".format(
        sorted(ui), sorted(RULES), sorted(ui ^ set(RULES))
    )
