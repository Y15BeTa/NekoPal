"""TTS 注册表：后端可用性与离线兜底（设计文档 4.7 默认音色策略）。"""

from nekopal.core.models import Capabilities
from nekopal.tts.macos_say import SayBackend
from nekopal.tts.registry import TTSRegistry


class _UnavailableBackend:
    """依赖没装 / 命令不存在的后端。"""

    id = "ghost"
    name = "Ghost"

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def available(self) -> bool:
        return False

    async def list_voices(self):
        return []

    async def synth(self, voice_id, text, options):
        raise RuntimeError("不可用")

    async def aclose(self):
        return None


def test_falls_back_when_backend_unavailable():
    registry = TTSRegistry()
    registry.register(_UnavailableBackend())
    registry.register(SayBackend())

    resolved = registry.resolve_voice_id("ghost:whatever", fallback="say:auto")
    assert resolved.startswith("say:")


def test_keeps_voice_when_backend_available():
    registry = TTSRegistry()
    registry.register(SayBackend())

    assert registry.resolve_voice_id("say:Tingting") == "say:Tingting"


def test_without_fallback_keeps_original_id():
    registry = TTSRegistry()
    registry.register(_UnavailableBackend())

    assert registry.resolve_voice_id("ghost:whatever") == "ghost:whatever"


def test_auto_resolves_to_concrete_voice():
    registry = TTSRegistry()
    registry.register(SayBackend())

    # 不能停在 "auto" 上，否则缓存键会不稳定
    assert registry.resolve_voice_id("say:auto") != "say:auto"
