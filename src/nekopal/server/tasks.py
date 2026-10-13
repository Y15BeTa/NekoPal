"""朗读任务注册表 + SSE 事件总线（设计文档 4.2 / 5.4 / 6.11）。

任务持有的是一个**可增长的句子流**（:class:`SentenceStream`）：

- 普通朗读：``streaming=False`` → 流创建时就装满并关闭，行为与以前完全一致
- **边写边读**（Follow）：``streaming=True`` → 流留开着，``append()`` 随时补新句子；
  念完已有的会**原地等**，直到 ``final`` 到来（或空闲超时）才收尾

增量追加的判定（最长公共前缀、冲突就不追）在 `core/append.py`，是纯函数、可离线单测。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from nekopal.core.append import plan_append, stable_prefix
from nekopal.core.cache import AudioCache
from nekopal.core.models import ReadRequest, SynthOptions
from nekopal.core.pipeline import SentenceStream, prepare_sentences, produce_segments

#: 队列终止哨兵
TERMINAL = object()

#: SSE 消费端的空转等待上限（秒）—— 用于在任务结束后优雅收尾
_POLL_INTERVAL = 0.25

#: Follow 模式下，多久没有新文本就自动收尾（防止"对方其实写完了但没发 final"留下僵尸任务）
FOLLOW_IDLE_SECONDS = 120.0


class ReadTask:
    def __init__(
        self,
        task_id: str,
        request: ReadRequest,
        sentences: List[str],
        options: SynthOptions,
        queue_size: int = 8,
        start_index: int = 0,
        fallback_voice: Optional[str] = None,
        streaming: bool = False,
    ) -> None:
        self.id = task_id
        self.request = request
        self.options = options
        self.start_index = max(0, start_index)
        self.fallback_voice = fallback_voice
        self.created_at = time.time()
        self.cancelled = False
        self.finished = False
        #: 是否「边写边读」模式（流保持打开，等 append）
        self.streaming = streaming
        #: 按播放顺序记下每段的 audio id —— 导出时直接复用这些缓存，不重新合成
        self.audio_ids: List[str] = []
        #: 因为"前文被改写"而跳过的追加次数（可观测性：出问题时有据可查）
        self.conflicts = 0
        self._last_append = time.time()
        #: **已提交**（= 已经送进去念的）句子列表，增量追加时做最长公共前缀比对。
        #: 流式模式下**从空开始** —— 只把"已定稿的句子"当已提交，否则最后那句一变大
        #: 就会被误判成"前文被改写"（冒烟测出来的坑）。
        self.submitted: List[str] = [] if streaming else list(sentences)
        self.stream = SentenceStream() if streaming else SentenceStream.of(sentences)
        self.stream.take_announcements()  # 初始那批由首帧 plan 播报，不必再报一次

        self._queue: "asyncio.Queue[Any]" = asyncio.Queue(maxsize=max(2, queue_size))
        self._runner: Optional["asyncio.Task[None]"] = None

    # -- 生命周期 --------------------------------------------------------
    def start(self, registry, cache: AudioCache) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(self._run(registry, cache))

    def cancel(self) -> None:
        self.cancelled = True
        if self._runner is not None and not self._runner.done():
            self._runner.cancel()

    @property
    def sentence_count(self) -> int:
        return len(self.stream)

    def append(self, text: str, *, final: bool = False, default_clean: List[str]) -> Dict[str, Any]:
        """把「消息到目前为止的全文」并进来。**同步**，绝不阻塞 HTTP。

        后端自己算增量（见 `core/append.py`）：客户端只管把最新全文发过来，
        重复发同一份不会有副作用。返回一个小报告，便于调试与观测。
        """

        self._last_append = time.time()
        if self.stream.closed:
            return {"appended": 0, "total": self.sentence_count, "closed": True}

        current = prepare_sentences(text, self.request.options, default_clean)
        delta = plan_append(self.submitted, current, final=final)

        if delta is None:
            # 前文被改写 → 不追加（已经念出去的收不回来，不能重念）
            self.conflicts += 1
            if final:
                self.stream.close()
            return {
                "appended": 0,
                "total": self.sentence_count,
                "conflict": True,
                "conflicts": self.conflicts,
            }

        self.submitted = list(current) if final else stable_prefix(current)
        start = self.stream.append(delta)
        if final:
            self.stream.close()
        return {
            "appended": len(delta),
            "total": self.sentence_count,
            "start_index": start,
        }

    async def _run(self, registry, cache: AudioCache) -> None:
        started = time.time()
        watchdog: Optional["asyncio.Task[None]"] = None
        try:
            await self.emit(
                "plan",
                {
                    "sentences": [
                        {"i": i, "text": sentence}
                        for i, sentence in enumerate(self.stream.snapshot())
                    ],
                    "append": False,
                    "streaming": self.streaming,
                },
            )
            if self.streaming:
                watchdog = asyncio.create_task(self._idle_watchdog())
            await produce_segments(
                self.stream,
                self.request.voice,
                self.options,
                registry,
                cache,
                self.emit,
                lambda: self.cancelled,
                start=self.start_index,
                fallback_voice=self.fallback_voice,
            )
            if not self.cancelled:
                await self.emit("done", {"elapsed": round(time.time() - started, 3)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜住一切，转成 error 事件
            try:
                await self.emit("error", {"code": "TASK_FAILED", "message": str(exc)})
            except Exception:  # noqa: BLE001
                pass
        finally:
            if watchdog is not None and not watchdog.done():
                watchdog.cancel()
            self.finished = True
            try:
                self._queue.put_nowait(TERMINAL)
            except asyncio.QueueFull:
                pass

    async def _idle_watchdog(self) -> None:
        """Follow 模式下的兜底：太久没有新文本就自己收尾。

        防的是"对方其实已经写完了，但 final 没发出来"—— 那会让任务永远挂着，
        客户端也就永远等不到 ``done``。
        """

        while not self.stream.closed and not self.cancelled:
            await asyncio.sleep(min(FOLLOW_IDLE_SECONDS, 5.0))
            if time.time() - self._last_append < FOLLOW_IDLE_SECONDS:
                continue
            try:
                await self.emit(
                    "error",
                    {
                        "code": "FOLLOW_IDLE",
                        "message": "{} 秒没有新内容，自动收尾".format(int(FOLLOW_IDLE_SECONDS)),
                        "fatal": False,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            self.stream.close()
            return

    # -- 事件 ------------------------------------------------------------
    async def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type == "segment" and data.get("audio"):
            self.audio_ids.append(str(data["audio"]).rsplit("/", 1)[-1])
        payload: Dict[str, Any] = {"task_id": self.id}
        payload.update(data)
        await self._queue.put({"type": event_type, "data": payload})

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """把队列转成 SSE 事件流；任务结束后自动收尾。"""

        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=_POLL_INTERVAL)
            except asyncio.TimeoutError:
                if self.finished and self._queue.empty():
                    return
                continue
            if event is TERMINAL:
                return
            yield event


class TaskRegistry:
    def __init__(self, max_tasks: int = 32) -> None:
        self._tasks: Dict[str, ReadTask] = {}
        self._order: List[str] = []
        self._max_tasks = max_tasks

    def create(
        self,
        request: ReadRequest,
        sentences: List[str],
        options: SynthOptions,
        queue_size: int = 8,
        start_index: int = 0,
        fallback_voice: Optional[str] = None,
        streaming: bool = False,
    ) -> ReadTask:
        task_id = "tsk_" + uuid.uuid4().hex[:8]
        task = ReadTask(
            task_id,
            request,
            sentences,
            options,
            queue_size=queue_size,
            start_index=start_index,
            fallback_voice=fallback_voice,
            streaming=streaming,
        )
        self._tasks[task_id] = task
        self._order.append(task_id)
        self._prune()
        return task

    def get(self, task_id: str) -> Optional[ReadTask]:
        return self._tasks.get(task_id)

    def cancel_all(self, keep: Optional[str] = None) -> int:
        count = 0
        for task_id, task in list(self._tasks.items()):
            if task_id == keep or task.finished:
                continue
            task.cancel()
            count += 1
        return count

    def remove(self, task_id: str) -> None:
        task = self._tasks.pop(task_id, None)
        if task is not None:
            task.cancel()
        if task_id in self._order:
            self._order.remove(task_id)

    def _prune(self) -> None:
        while len(self._order) > self._max_tasks:
            oldest = self._order.pop(0)
            task = self._tasks.pop(oldest, None)
            if task is not None and not task.finished:
                task.cancel()
