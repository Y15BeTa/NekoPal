"""增量追加：把「还在长的全文」变成「可以立刻念的新句子」（设计文档 6.11 Follow）。

为什么不能直接做 ``split(新文本) - split(旧文本)``？
--------------------------------------------------
`split_sentences` 末尾有一个「**过短的句子并进下一句**」的合并步骤
（见 `core/split.py` 的 `_merge_short`），所以：

- 列表里**除最后一句之外**的句子都已经**定稿**（不会再变）
- **最后一句**会随着后续文本继续变长 —— 它还可能被吸收掉

所以增量追加的规则是：

1. 取「去掉最后一句」的结果作为**当前可念的稳定前缀**
2. 跟「上次已提交的句子列表」做**最长公共前缀**比对
3. 前缀对得上 → 多的那几句就是新增，可以立刻送去念
4. 对不上 → 说明**对方改写了前文** → **一句都不追加**

第 4 条是刻意的：**已经念出去的内容收不回来**，宁可不补，
也不要把同一句再念一遍（那比"晚半秒"难受得多）。

本模块**只有纯函数**：不碰网络、不碰磁盘，因此可以离线单测。
"""

from __future__ import annotations

from typing import List, Optional


def stable_prefix(sentences: List[str]) -> List[str]:
    """去掉最后一句 —— 它还可能被后续文本改写。

    只剩一句时返回空列表（那一句还没定稿）。
    """

    return list(sentences[:-1]) if len(sentences) > 1 else []


def plan_append(
    previous: List[str],
    current: List[str],
    *,
    final: bool = False,
) -> Optional[List[str]]:
    """算出**应当追加**的句子。

    Args:
        previous: 上次已提交（= 已经送去念的）句子列表
        current: 把「新版本全文」重新清洗分句后的结果
        final: 这一轮是否已经生成完（是的话连最后一句也收）

    Returns:
        要追加的句子列表（可能是空的）；返回 ``None`` 表示**无法安全追加**
        —— 前文被改写了，调用方应当跳过这次追加（而不是重念）。
    """

    target = list(current) if final else stable_prefix(current)

    # 变短了 → 一定被改写
    if len(target) < len(previous):
        return None

    # 最长公共前缀必须**覆盖全部的 previous**
    for index, sentence in enumerate(previous):
        if target[index] != sentence:
            return None

    return target[len(previous):]
