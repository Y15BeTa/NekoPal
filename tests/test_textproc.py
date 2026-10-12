"""文本清洗链（设计文档 5.1）。"""

from nekopal.core.textproc import DEFAULT_ORDER, apply_clean
from nekopal.core.textproc.chain import available_rules


def test_default_order_is_documented_order():
    assert available_rules() == DEFAULT_ORDER
    # skip_code 必须早于 strip_markdown，否则代码围栏会被破坏
    assert DEFAULT_ORDER.index("skip_code") < DEFAULT_ORDER.index("strip_markdown")


def test_skip_code_removes_fenced_block():
    text = "说明一下。\n```python\nprint('hi')\n```\n然后结束。"
    cleaned = apply_clean(text)
    assert "print" not in cleaned
    assert "说明一下" in cleaned


def test_inline_code_keeps_content():
    cleaned = apply_clean("这里用 `attention` 这个词。")
    assert "attention" in cleaned
    assert "`" not in cleaned


def test_strip_markdown_removes_markers():
    cleaned = apply_clean("## 标题\n\n这是**重点**内容，见[文档](https://example.com)。")
    assert "**" not in cleaned
    assert "#" not in cleaned
    assert "https://" not in cleaned
    assert "重点" in cleaned
    assert "文档" in cleaned


def test_skip_tables_drops_table_rows():
    text = "表格如下：\n| 列一 | 列二 |\n| --- | --- |\n| 甲 | 乙 |\n表格结束。"
    cleaned = apply_clean(text)
    assert "列一" not in cleaned
    assert "表格如下" in cleaned


def test_speak_symbols_reads_aloud():
    cleaned = apply_clean("执行 A → B，且 x ≥ 3。")
    assert "箭头" in cleaned
    assert "大于等于" in cleaned


def test_normalize_strips_zero_width_and_fullwidth_digits():
    cleaned = apply_clean("全角\uff11\uff12\uff13\u200b测试")
    assert "123" in cleaned
    assert "\u200b" not in cleaned


def test_emoji_removed_but_arrows_survive_as_words():
    cleaned = apply_clean("好耶 🎉 完成 ✅ 再看 → 这里")
    assert "🎉" not in cleaned
    assert "✅" not in cleaned
    assert "箭头" in cleaned


def test_subset_of_rules_can_be_disabled():
    text = "这是**重点**内容。"
    cleaned = apply_clean(text, enabled=["normalize"])
    assert "**" in cleaned
