"""分句规则（设计文档 5.2）。"""

from nekopal.core.split import split_sentences


def test_empty_text_returns_nothing():
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []


def test_splits_on_chinese_punctuation():
    text = "第一句话很长很长很长。第二句话也很长很长很长。"
    assert split_sentences(text) == ["第一句话很长很长很长。", "第二句话也很长很长很长。"]


def test_splits_on_english_punctuation():
    text = "This is a fairly long sentence. This is another long sentence."
    result = split_sentences(text)
    assert len(result) == 2
    assert result[0].startswith("This is a fairly")


def test_short_sentence_merges_with_next():
    text = "短。这是一句比较长的话。"
    assert split_sentences(text) == ["短。这是一句比较长的话。"]


def test_trailing_short_fragment_merges_back():
    text = "这是一句比较长的话。尾巴。"
    result = split_sentences(text)
    assert len(result) == 1
    assert result[0].endswith("尾巴。")


def test_newlines_act_as_boundaries():
    text = "第一行也得够长才不会被合并\n第二行同样要够长才不会被合并"
    assert len(split_sentences(text)) == 2


def test_mixed_chinese_english_keeps_terms():
    text = "我们今天聊 attention 机制。它是 Transformer 的核心。"
    result = split_sentences(text)
    assert len(result) == 2
    assert "attention" in result[0]
