"""朗读任务注册表 + SSE 事件总线（设计文档 4.2 / 5.4）。"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from nekopal.core.cache import AudioCache
from nekopal.core.models import ReadRequest, SynthOptions
from nekopal.core.pipeline import produce_segments

#: 队列终止哨兵
TERMINAL = object()

#: SSE 消费端的空转等待上限（秒）—— 用于在任务结束后优雅收尾
_POLL_INTERVAL = 0.25


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
    ) -> None:
        self.id = task_id
        self.request = request
        self.sentences = sentences
        self.options = options
        self.start_index = max(0, start_index)
        self.fallback_voice = fallback_voice
        self.created_at = time.time()
        self.cancelled = False
        self.finished = False
        #: 按播放顺序记下每段的 audio id —— 导出时直接复用这些缓存，不重新合成
        self.audio_ids: List[str] = []
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

    async def _run(self, registry, cache: AudioCache) -> None:
        started = time.time()
        try:
            await self.emit(
                "plan",
                {
                    "sentences": [
                        {"i": i, "text": sentence}
                        for i, sentence in enumerate(self.sentences)
                    ],
                    "append": False,
                },
            )
            await produce_segments(
                self.sentences,
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
            self.finished = True
            try:
                self._queue.put_nowait(TERMINAL)
            except asyncio.QueueFull:
                pass

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
