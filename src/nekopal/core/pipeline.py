"""朗读编排：清洗 → 分句 → 预合成队列（设计文档 5.4）。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from nekopal.core.cache import AudioCache, CachedSegment
from nekopal.core.models import SynthOptions
from nekopal.core.split import split_sentences
from nekopal.core.textproc import apply_clean

EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]


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
) -> CachedSegment:
    """合成一句；命中缓存则直接复用。"""

    key = cache.key(sentence, voice_id, options)
    hit = cache.load(key)
    if hit is not None:
        return hit
    result = await registry.synth(voice_id, sentence, options)
    segment = cache.store(key, sentence, voice_id, result)
    cache.evict()
    return segment


async def produce_segments(
    sentences: List[str],
    voice_id: str,
    options: SynthOptions,
    registry,
    cache: AudioCache,
    emit: EmitFn,
    is_cancelled: Callable[[], bool],
    start: int = 0,
) -> None:
    """从 ``start`` 开始逐句合成并推送，始终保持「下一句已在合成中」。

    当任务队列写满时 ``emit`` 会阻塞，从而形成**背压**——
    生产者最多领先客户端若干句，既掩盖了合成延迟，又不会撑爆内存。
    """

    total = len(sentences)
    if total == 0 or start >= total:
        return

    def _synth(index: int) -> "asyncio.Task[CachedSegment]":
        return asyncio.create_task(
            synthesize_sentence(sentences[index], voice_id, options, registry, cache)
        )

    pending: Optional["asyncio.Task[CachedSegment]"] = None
    try:
        for i in range(start, total):
            if is_cancelled():
                break
            if pending is None:
                pending = _synth(i)
            try:
                segment = await pending
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
                        "segment_index": i,
                    },
                )
                continue

            # 先把下一句挂上，再去 emit；这样合成与客户端消费（播放）重叠
            if i + 1 < total:
                pending = _synth(i + 1)

            await emit("segment", segment.to_event(i))
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
