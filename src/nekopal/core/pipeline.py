"""朗读编排：清洗 → 分句 → 预合成队列（设计文档 5.4 / 6.11）。

「边写边读」（Follow）的关键抽象是 :class:`SentenceStream` ——
它把原来"一个固定列表"换成"**可以边产边长、并且能被等待**的句子序列"，
于是 ``produce_segments`` 就能在"对方还在写"的时候原地等新句子。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from nekopal.core.cache import AudioCache, CachedSegment
from nekopal.core.models import SynthOptions
from nekopal.core.split import split_sentences
from nekopal.core.textproc import apply_clean

EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]

#: 等待新句子时的兜底轮询间隔（正常情况下靠事件唤醒，这里只是保险）
_WAIT_TICK = 0.2


class SentenceStream:
    """**可增长的**句子流：调用方随时追加，消费者可以等下一句。

    - 普通朗读：创建时就装满并 ``close()``，行为与"固定列表"完全一致
    - 边写边读：先给已有的，之后 ``append()`` 补，最后 ``close()``

    追加时会记下"待播报的批次"，由**消费者**（任务运行器）在合适的时机
    emit ``plan{append: true}`` —— 这样能保证「先播报、后送音频」的顺序，
    也避免 HTTP 请求因为队列写满而卡住。
    """

    def __init__(self, sentences: Optional[List[str]] = None, *, closed: bool = False) -> None:
        self._items: List[str] = list(sentences or [])
        self._closed = closed
        self._changed: asyncio.Event = asyncio.Event()
        self._announce: List[Tuple[int, List[str]]] = []

    @classmethod
    def of(cls, sentences: List[str]) -> "SentenceStream":
        """固定列表 → 已关闭的流（普通朗读走这条，行为不变）。"""

        return cls(sentences, closed=True)

    # -- 生产方 ----------------------------------------------------------
    def append(self, sentences: List[str]) -> int:
        """追加若干句，返回**第一句的下标**。空列表不产生播报批次。"""

        start = len(self._items)
        if sentences:
            self._items.extend(sentences)
            self._announce.append((start, list(sentences)))
            self._changed.set()
        return start

    def close(self) -> None:
        self._closed = True
        self._changed.set()

    # -- 消费方 ----------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> str:
        return self._items[index]

    def snapshot(self) -> List[str]:
        return list(self._items)

    def has(self, index: int) -> bool:
        """第 ``index`` 句现在有吗（**不等待**，用于预合成的提前量）。"""

        return index < len(self._items)

    async def wait_for(self, index: int) -> bool:
        """等到第 ``index`` 句可用。流已关闭且仍不够长时返回 ``False``。"""

        while True:
            if index < len(self._items):
                return True
            if self._closed:
                return False
            event = asyncio.Event()
            self._changed = event  # 换上新事件，之后 append/close 会 set 它
            # 复查：换事件之前可能已经到了
            if index < len(self._items) or self._closed:
                continue
            try:
                await asyncio.wait_for(event.wait(), timeout=_WAIT_TICK)
            except asyncio.TimeoutError:
                pass  # 兜底轮询：防止任何遗漏的唤醒

    def take_announcements(self) -> List[Tuple[int, List[str]]]:
        """取走待播报的批次（消费方负责 emit ``plan{append: true}``）。"""

        batches, self._announce = self._announce, []
        return batches


def prepare_sentences(text: str, options: Dict[str, Any], default_clean: List[str]) -> List[str]:
    """清洗 + 分句。"""

    enabled = options.get("clean")
    if not isinstance(enabled, list) or not enabled:
        enabled = default_clean
    cleaned = apply_clean(text, enabled=enabled)
    max_chars = int(options.get("max_chars") or 0)
    if max_chars > 0:
        cleaned = cleaned[:max_chars]
    return split_sentences(cleaned)


async def synthesize_sentence(
    sentence: str,
    voice_id: str,
    options: SynthOptions,
    registry,
    cache: AudioCache,
    fallback_voice: Optional[str] = None,
) -> Tuple[CachedSegment, bool]:
    """合成一句；命中缓存则直接复用。

    返回 ``(segment, used_fallback)``。
    主音色失败时（比如联网音色断网了）自动改用 ``fallback_voice``，
    并**用后备音色自己的缓存键**存下来 —— 这样恢复联网后主音色仍然可用。
    """

    key = cache.key(sentence, voice_id, options)
    hit = cache.load(key)
    if hit is not None:
        return hit, False

    try:
        result = await registry.synth(voice_id, sentence, options)
    except Exception:
        if not fallback_voice or fallback_voice == voice_id:
            raise
        fallback_key = cache.key(sentence, fallback_voice, options)
        hit = cache.load(fallback_key)
        if hit is not None:
            return hit, True
        result = await registry.synth(fallback_voice, sentence, options)
        return cache.store(fallback_key, sentence, fallback_voice, result), True

    segment = cache.store(key, sentence, voice_id, result)
    cache.evict()
    return segment, False


async def produce_segments(
    stream: "SentenceStream | List[str]",
    voice_id: str,
    options: SynthOptions,
    registry,
    cache: AudioCache,
    emit: EmitFn,
    is_cancelled: Callable[[], bool],
    start: int = 0,
    fallback_voice: Optional[str] = None,
) -> None:
    """从 ``start`` 开始逐句合成并推送，始终保持「下一句已在合成中」。

    句子来自 :class:`SentenceStream` —— **它可能还在增长**：
    没有下一句时会原地等待，流关闭且念完才返回（这就是"边写边读"）。

    为方便调用方，这里也接受**普通列表**（等于一个已关闭的流）——
    固定朗读场景就直接传列表。

    当任务队列写满时 ``emit`` 会阻塞，从而形成**背压** ——
    生产者最多领先客户端若干句，既掩盖了合成延迟，又不会撑爆内存。
    """

    # 传列表 = 一个「已关闭的流」，两者行为完全一致
    sentences = (
        stream if isinstance(stream, SentenceStream) else SentenceStream.of(list(stream))
    )

    def _synth(index: int) -> "asyncio.Task[Tuple[CachedSegment, bool]]":
        return asyncio.create_task(
            synthesize_sentence(
                sentences[index], voice_id, options, registry, cache, fallback_voice
            )
        )

    index = start
    pending: Optional["asyncio.Task[Tuple[CachedSegment, bool]]"] = None
    fallback_notified = False
    try:
        while not is_cancelled():
            if not await sentences.wait_for(index):
                break  # 流已关闭且念完了
            if is_cancelled():
                break

            # 先把「这一批新句子」播报出去，再送它们的音频（顺序不能反）
            for batch_start, batch in sentences.take_announcements():
                await emit(
                    "plan",
                    {
                        "append": True,
                        "sentences": [
                            {"i": batch_start + offset, "text": sentence}
                            for offset, sentence in enumerate(batch)
                        ],
                    },
                )

            if pending is None:
                pending = _synth(index)
            try:
                segment, used_fallback = await pending
                pending = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单句失败不应中断整个任务
                pending = None
                await emit(
                    "error",
                    {
                        "code": "TTS_FAILED",
                        "message": str(exc),
                        "segment_index": index,
                    },
                )
                index += 1
                continue

            if used_fallback and not fallback_notified:
                fallback_notified = True
                await emit(
                    "error",
                    {
                        "code": "FALLBACK_VOICE",
                        "message": "{} 不可用，已自动回退到 {}（离线兜底）".format(
                            voice_id, fallback_voice
                        ),
                        "fatal": False,
                    },
                )

            # 先把下一句挂上，再去 emit；这样合成与客户端消费（播放）重叠
            if sentences.has(index + 1):
                pending = _synth(index + 1)

            await emit("segment", segment.to_event(index))
            index += 1
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
