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

#: 假变声命令：帧不动，只把采样率改写成 48k —— 模拟真实 RVC 的输出（实测就是 48k）
RELABEL_RATE_COMMAND = [
    sys.executable,
    "-c",
    (
        "import sys, wave\n"
        "src = wave.open(sys.argv[1])\n"
        "frames = src.readframes(src.getnframes())\n"
        "dst = wave.open(sys.argv[2], 'wb')\n"
        "dst.setnchannels(src.getnchannels())\n"
        "dst.setsampwidth(src.getsampwidth())\n"
        "dst.setframerate(48000)\n"
        "dst.writeframes(frames)\n"
        "dst.close()\n"
    ),
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

    def __init__(self, words=None, rate: int = 24000) -> None:
        self.calls = []
        self.words = list(words or [])
        self.rate = rate

    async def synth(self, voice_id, text, options):
        self.calls.append((voice_id, text))
        data = target_wav_bytes(0.25, rate=self.rate)
        return SynthResult(
            audio=data,
            sample_rate=self.rate,
            duration=0.25,
            words=list(self.words),
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


def test_word_timings_pass_through_from_base_voice():
    """变声不改动时间轴，所以 base 的词级时间戳要**透传**（不再一律降级句级）。

    实测依据：5.136s 的 base 音频经 RVC 后为 5.120s，漂移仅 **16ms（≈0.31%）**。
    因此前端照常逐词高亮，而不是退回整句高亮。
    """

    from nekopal.core.models import WordTiming

    words = [
        WordTiming(t0=0.0, t1=0.1, text="你好"),
        WordTiming(t0=0.1, t1=0.25, text="世界"),
    ]
    backend = RvcBackend(settings_with())
    backend.attach(FakeRegistry(words=words), lambda voice_id: FakePack())

    result = asyncio.run(backend.synth("rvc:demo", "你好世界", SynthOptions()))

    assert [(w.text, w.t0, w.t1) for w in result.words] == [
        ("你好", 0.0, 0.1),
        ("世界", 0.1, 0.25),
    ]


def test_word_timings_stay_empty_when_base_has_none():
    """base 音色本身没有时间戳（如 say）时，优雅退回句级 —— 不是报错。"""

    backend = RvcBackend(settings_with())
    backend.attach(FakeRegistry(words=[]), lambda voice_id: FakePack())

    result = asyncio.run(backend.synth("rvc:demo", "文本", SynthOptions()))
    assert result.words == []


def test_sample_rate_read_from_output_wav():
    """采样率由变声工具决定，**不能写死**。

    踩过：这里原写 `sample_rate=24000`，但实测真实 RVC 输出是 **48000 Hz**
    （``convert_cli.py`` 里的 ``out_rate``）。虽然当时没人读这个字段，
    但留着就是一条"撒谎的元数据"。
    """

    backend = RvcBackend(settings_with(command=RELABEL_RATE_COMMAND))
    backend.attach(FakeRegistry(), lambda voice_id: FakePack())

    result = asyncio.run(backend.synth("rvc:demo", "文本", SynthOptions()))

    assert result.sample_rate == 48000


def test_base_audio_is_normalised_to_24k_before_conversion():
    """base 音频在交给变声工具**之前**会被归一化成 24k / 单声道 / 16bit。

    这是刻意的（统一"给 WAV 还 WAV"的契约），但以前没写在文档里 —— 属于隐含逻辑。
    证据：让 base 产出 40k，再让假命令原样复制 → 结果只剩 24k。
    """

    backend = RvcBackend(settings_with())  # COPY_COMMAND：把输入原样复制
    backend.attach(FakeRegistry(rate=40000), lambda voice_id: FakePack())

    result = asyncio.run(backend.synth("rvc:demo", "文本", SynthOptions()))

    assert result.sample_rate == 24000  # 40k 的 base 被规范化成 24k 后才喂进去


def test_synth_result_default_sample_rate_when_unparsable():
    """输出不是合法 WAV 时，采样率回退到默认值而不是 0（不要制造新的假数据）。"""

    from nekopal.media import FALLBACK_SAMPLE_RATE, wav_sample_rate

    assert wav_sample_rate(b"not a wav at all") == FALLBACK_SAMPLE_RATE


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
