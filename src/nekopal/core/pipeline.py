"""朗读编排：清洗 → 分句 → 预合成队列（设计文档 5.4）。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

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
    sentences: List[str],
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

    当任务队列写满时 ``emit`` 会阻塞，从而形成**背压**——
    生产者最多领先客户端若干句，既掩盖了合成延迟，又不会撑爆内存。
    """

    total = len(sentences)
    if total == 0 or start >= total:
        return

    def _synth(index: int) -> "asyncio.Task[Tuple[CachedSegment, bool]]":
        return asyncio.create_task(
            synthesize_sentence(
                sentences[index], voice_id, options, registry, cache, fallback_voice
            )
        )

    pending: Optional["asyncio.Task[Tuple[CachedSegment, bool]]"] = None
    fallback_notified = False
    try:
        for i in range(start, total):
            if is_cancelled():
                break
            if pending is None:
                pending = _synth(i)
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
                        "segment_index": i,
                    },
                )
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
            if i + 1 < total:
                pending = _synth(i + 1)

            await emit("segment", segment.to_event(i))
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
