"""外部推送抓取源（本轮 MVP 的主路径）。

文本由外部程序通过 ``POST /api/read`` 送入 ——
opencode 插件、编辑器插件、脚本都可以走这里。
它不需要任何系统权限，也不污染剪贴板。
"""

from __future__ import annotations

from typing import Callable

from nekopal.capture.base import CaptureInfo


class HttpInSource:
    id = "http_in"
    name = "外部推送（HTTP）"
    platform = "any"
    granularity = "block"

    def __init__(self) -> None:
        self._active = True

    def info(self) -> CaptureInfo:
        return CaptureInfo(
            id=self.id,
            name=self.name,
            platform=self.platform,
            granularity=self.granularity,
            status="active" if self._active else "planned",
            description="外部程序（如 opencode 插件）POST 一整块文本，无需系统权限。",
        )

    def start(self, emit: Callable[[dict], None]) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False


class MacSelectionSource:
    """macOS 选区抓取（Phase 1 可选，本轮未实现）。"""

    id = "macos_ax"
    name = "系统选区（全局快捷键）"
    platform = "macos"
    granularity = "selection"

    def info(self) -> CaptureInfo:
        return CaptureInfo(
            id=self.id,
            name=self.name,
            platform=self.platform,
            granularity=self.granularity,
            status="planned",
            description="全局快捷键 + 辅助功能 API 取选区；需要用户授权。",
        )

    def start(self, emit: Callable[[dict], None]) -> None:
        raise NotImplementedError("macos_ax 尚未实现（Phase 1 可选 / Phase 2）")

    def stop(self) -> None:
        return None


class FileWatchSource:
    """本地文件抓取（Phase 2）。"""

    id = "file_watch"
    name = "本地文件"
    platform = "any"
    granularity = "block"

    def info(self) -> CaptureInfo:
        return CaptureInfo(
            id=self.id,
            name=self.name,
            platform=self.platform,
            granularity=self.granularity,
            status="planned",
            description="打开或监听本地 Markdown / 文档，跟读窗自渲染整篇。",
        )

    def start(self, emit: Callable[[dict], None]) -> None:
        raise NotImplementedError("file_watch 尚未实现（Phase 2）")

    def stop(self) -> None:
        return None
