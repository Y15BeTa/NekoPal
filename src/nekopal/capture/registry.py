"""抓取源注册表。"""

from __future__ import annotations

from typing import Any, Dict, List

from nekopal.capture.base import CaptureSource
from nekopal.capture.http_in import FileWatchSource, HttpInSource, MacSelectionSource


class CaptureRegistry:
    def __init__(self) -> None:
        self._sources: List[CaptureSource] = []

    def register(self, source: CaptureSource) -> None:
        self._sources.append(source)

    def all(self) -> List[CaptureSource]:
        return list(self._sources)

    def describe(self) -> List[Dict[str, Any]]:
        return [source.info().to_dict() for source in self._sources]


def build_capture_registry() -> CaptureRegistry:
    registry = CaptureRegistry()
    registry.register(HttpInSource())
    registry.register(MacSelectionSource())
    registry.register(FileWatchSource())
    return registry
