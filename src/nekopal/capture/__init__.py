"""抓取层插件（设计文档 6.2）。"""

from nekopal.capture.base import CaptureInfo, CaptureSource
from nekopal.capture.registry import CaptureRegistry, build_capture_registry

__all__ = ["CaptureInfo", "CaptureSource", "CaptureRegistry", "build_capture_registry"]
