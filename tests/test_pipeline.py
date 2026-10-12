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


def test_prepare_sentences_cleans_then_splits():
    text = "## 标题\n\n```python\nprint('x')\n```\n\n这是**正文**的第一句话。它是第二句话。"
    sentences = prepare_sentences(text, {}, ["normalize", "skip_code", "strip_markdown"])
    joined = "".join(sentences)
    assert "print" not in joined
    assert "**" not in joined
    assert "标题" in joined
