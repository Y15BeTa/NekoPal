"""变声后端：组合管线（基础音色合成 → 变声）（设计文档 6.x）。"""

import asyncio
import io
import sys
import wave
from types import SimpleNamespace

import pytest

from nekopal.core.models import SynthOptions, SynthResult
from nekopal.tts.registry import build_registry
from nekopal.tts.rvc import RvcBackend

COPY_COMMAND = [
    sys.executable,
    "-c",
    "import shutil,sys; shutil.copy(sys.argv[1], sys.argv[2])",
    "{input}",
    "{output}",
]


def target_wav_bytes(seconds: float = 0.3, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class FakeRegistry:
    """假装自己是 TTSRegistry，记录被请求的基础音色。"""

    def __init__(self) -> None:
        self.calls = []

    async def synth(self, voice_id, text, options):
        self.calls.append((voice_id, text))
        data = target_wav_bytes(0.25)
        return SynthResult(
            audio=data,
            sample_rate=24000,
            duration=0.25,
            words=[],
            media_type="audio/wav",
            extension="wav",
        )


class FakePack:
    id = "demo"
    base_voice = "edge:zh-CN-XiaoxiaoNeural"
    rvc_model = "/tmp/demo.pth"
    rvc_index = None
    command = None


def settings_with(command=COPY_COMMAND):
    return SimpleNamespace(rvc_command=list(command), rvc_timeout=60.0)


def build_backend(command=COPY_COMMAND, pack=FakePack()):
    backend = RvcBackend(settings_with(command))
    registry = FakeRegistry()
    backend.attach(registry, lambda voice_id: pack)
    return backend, registry


# ---- 装配与能力 -----------------------------------------------------------

def test_id_and_capabilities():
    backend, _ = build_backend()
    assert backend.id == "rvc"
    caps = backend.capabilities()
    assert caps.word_timings is False   # 变声不产出词级时间戳
    assert caps.offline is True


def test_available_requires_a_command():
    assert build_backend()[0].available() is True
    assert RvcBackend(settings_with(command=[])).available() is False


def test_list_voices_is_empty_by_design():
    """变声音色全部来自用户自己的音色包，后端本身不提供任何音色。"""

    backend, _ = build_backend()
    assert asyncio.run(backend.list_voices()) == []


def test_stamp_capabilities_takes_them_from_the_backend(tmp_path):
    """音色包只声明"用哪个后端"；能力必须以后端为准（设计文档 6.7）。

    踩过：包音色一度报 rate=False / pitch=False（Voice 的默认值），
    而 rvc 后端实际是 rate=True / pitch=True → 界面的能力协商读到假信息。
    """

    from nekopal.core.models import Voice

    registry = build_registry(SimpleNamespace(rvc_command=[], rvc_timeout=60.0))

    from_pack = Voice(id="rvc:demo", name="包音色", backend="rvc")
    assert from_pack.capabilities.rate is False  # 默认值：确实没有能力

    registry.stamp_capabilities([from_pack])
    assert from_pack.capabilities.rate is True
    assert from_pack.capabilities.pitch is True
    assert from_pack.capabilities.word_timings is False


def test_stamp_capabilities_ignores_unknown_backend():
    from nekopal.core.models import Voice

    registry = build_registry(SimpleNamespace(rvc_command=[], rvc_timeout=60.0))
    orphan = Voice(id="ghost:x", name="幽灵", backend="ghost")
    registry.stamp_capabilities([orphan])
    assert orphan.capabilities.rate is False  # 找不到后端就保持原样，不报错


# ---- 组合管线 -------------------------------------------------------------

def test_synth_uses_base_voice_then_converts():
    backend, registry = build_backend()

    result = asyncio.run(backend.synth("rvc:demo", "要读的文本", SynthOptions()))

    # ① 先用基础音色合成
    assert registry.calls == [("edge:zh-CN-XiaoxiaoNeural", "要读的文本")]
    # ② 再变声：假命令原样复制，所以时长与输入一致
    assert result.extension == "wav"
    assert result.media_type == "audio/wav"
    assert result.words == []
    assert result.duration == pytest.approx(0.25, abs=0.02)


def test_synth_errors_when_pack_missing():
    backend, _ = build_backend(pack=None)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(backend.synth("rvc:ghost", "文本", SynthOptions()))
    assert "音色包" in str(excinfo.value)


def test_synth_errors_when_base_voice_missing():
    class NoBase:
        id = "bad"
        base_voice = None
        rvc_model = "/tmp/x.pth"
        rvc_index = None
        command = None

    backend, _ = build_backend(pack=NoBase())
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(backend.synth("rvc:bad", "文本", SynthOptions()))
    assert "base_voice" in str(excinfo.value)


def test_pack_level_command_overrides_global():
    class PackWithCommand(FakePack):
        command = [sys.executable, "-c", "import sys; sys.exit(7)"]

    backend, _ = build_backend(pack=PackWithCommand())
    from nekopal.core.convert import ConvertError

    with pytest.raises(ConvertError) as excinfo:
        asyncio.run(backend.synth("rvc:demo", "文本", SynthOptions()))
    assert "退出码 7" in str(excinfo.value)


def test_synth_errors_without_any_command():
    backend, _ = build_backend(command=[])
    from nekopal.core.convert import ConvertError

    with pytest.raises(ConvertError):
        asyncio.run(backend.synth("rvc:demo", "文本", SynthOptions()))
