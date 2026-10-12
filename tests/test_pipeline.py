"""预合成队列（设计文档 5.4）—— 全部离线，不发网络请求。"""

import asyncio
from typing import Any, Dict, List, Tuple

from nekopal.core.cache import AudioCache
from nekopal.core.models import SynthOptions
from nekopal.core.pipeline import prepare_sentences, produce_segments
from tests.fakes.silent_backend import FailingBackend, SilentBackend

SENTENCES = ["第一句话很长很长很长。", "第二句话也很长很长很长。", "第三句话同样很长很长。"]


def _collect(backend, tmp_path, sentences=SENTENCES, start=0, cancelled=lambda: False):
    cache = AudioCache(tmp_path, max_mb=10)
    events: List[Tuple[str, Dict[str, Any]]] = []

    async def emit(event_type: str, data: Dict[str, Any]) -> None:
        events.append((event_type, data))

    async def run() -> None:
        await produce_segments(
            sentences,
            "fake:test",
            SynthOptions(),
            backend,
            cache,
            emit,
            cancelled,
            start=start,
        )

    asyncio.run(run())
    return events, backend


def test_emits_one_segment_per_sentence(tmp_path):
    events, _ = _collect(SilentBackend(), tmp_path)
    assert [e[0] for e in events] == ["segment"] * 3
    assert [e[1]["i"] for e in events] == [0, 1, 2]
    assert events[0][1]["audio"].startswith("/api/audio/")


def test_cache_hit_avoids_second_synthesis(tmp_path):
    backend = SilentBackend()
    _collect(backend, tmp_path)
    assert len(backend.calls) == 3

    _collect(backend, tmp_path)  # 同一个 tmp_path → 命中缓存
    assert len(backend.calls) == 3


def test_start_index_skips_earlier_sentences(tmp_path):
    events, backend = _collect(SilentBackend(), tmp_path, start=1)
    assert [e[1]["i"] for e in events] == [1, 2]
    assert backend.calls == SENTENCES[1:]


def test_single_failure_does_not_abort_task(tmp_path):
    backend = FailingBackend(fail_texts=[SENTENCES[1]])
    events, _ = _collect(backend, tmp_path)

    kinds = [e[0] for e in events]
    assert kinds == ["segment", "error", "segment"]
    error = events[1][1]
    assert error["code"] == "TTS_FAILED"
    assert error["segment_index"] == 1
    assert [e[1]["i"] for e in events if e[0] == "segment"] == [0, 2]


def test_cancellation_stops_emitting(tmp_path):
    state = {"n": 0}

    def cancelled() -> bool:
        state["n"] += 1
        return state["n"] > 2

    events, _ = _collect(SilentBackend(), tmp_path, cancelled=cancelled)
    assert len(events) < 3


class _RoutingRegistry:
    """按 voice_id 前缀把请求分派给不同后端，用于验证兜底。"""

    def __init__(self, primary, fallback) -> None:
        self.primary = primary
        self.fallback = fallback
        self.calls: List[str] = []

    async def synth(self, voice_id, text, options):
        self.calls.append(voice_id)
        backend = self.primary if voice_id.startswith("edge:") else self.fallback
        return await backend.synth(voice_id, text, options)


def _collect_with_fallback(registry, tmp_path, fallback_voice, sentences=SENTENCES):
    cache = AudioCache(tmp_path, max_mb=10)
    events: List[Tuple[str, Dict[str, Any]]] = []

    async def emit(event_type: str, data: Dict[str, Any]) -> None:
        events.append((event_type, data))

    async def run() -> None:
        await produce_segments(
            sentences,
            "edge:primary",
            SynthOptions(),
            registry,
            cache,
            emit,
            lambda: False,
            fallback_voice=fallback_voice,
        )

    asyncio.run(run())
    return events, cache


def test_falls_back_to_offline_voice_and_notifies_once(tmp_path):
    sentinels = FailingBackend(fail_texts=list(SENTENCES))
    offline = SilentBackend()
    registry = _RoutingRegistry(primary=sentinels, fallback=offline)

    events, cache = _collect_with_fallback(registry, tmp_path, "say:offline")

    # FALLBACK_VOICE 是错误**码**，事件类型仍然是 "error"
    codes = [data.get("code") for kind, data in events if kind == "error"]
    assert codes.count("FALLBACK_VOICE") == 1, "兜底提示只应出现一次"
    assert "TTS_FAILED" not in codes, "兜底成功后不应再报合成失败"
    assert [d["i"] for k, d in events if k == "segment"] == [0, 1, 2]

    # 兜底音频落在后备音色**自己的**缓存键上，主音色键保持为空
    assert cache.load(cache.key(SENTENCES[0], "say:offline", SynthOptions())) is not None
    assert cache.load(cache.key(SENTENCES[0], "edge:primary", SynthOptions())) is None


def test_no_fallback_still_reports_error(tmp_path):
    sentinels = FailingBackend(fail_texts=list(SENTENCES))
    registry = _RoutingRegistry(primary=sentinels, fallback=SilentBackend())

    events, _ = _collect_with_fallback(registry, tmp_path, fallback_voice=None)

    kinds = [kind for kind, _ in events]
    assert kinds == ["error"] * 3
    assert all(d["code"] == "TTS_FAILED" for _, d in events)


def test_prepare_sentences_cleans_then_splits():
    text = "## 标题\n\n```python\nprint('x')\n```\n\n这是**正文**的第一句话。它是第二句话。"
    sentences = prepare_sentences(text, {}, ["normalize", "skip_code", "strip_markdown"])
    joined = "".join(sentences)
    assert "print" not in joined
    assert "**" not in joined
    assert "标题" in joined
