"""TTS 后端插件（设计文档 6.1）。"""

from nekopal.tts.base import TTSBackend
from nekopal.tts.registry import TTSRegistry, build_registry

__all__ = ["TTSBackend", "TTSRegistry", "build_registry"]
