"""音频导出（设计文档 5.8）。

全部离线：不联网、不调用真实 TTS。需要 `afconvert` 的用例会自动跳过（非 macOS 环境）。
"""

import shutil
import wave
from pathlib import Path

import pytest

from nekopal.core.export import (
    TARGET_RATE,
    ExportError,
    assemble,
    available_formats,
    concat,
    extension_for,
    is_target_wav,
    make_silence,
    missing_tool,
    write_wav,
)


def make_wav(path: Path, seconds: float, rate: int = TARGET_RATE, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds * channels))
    return path


# ---- 纯函数 ---------------------------------------------------------------

def test_silence_duration():
    assert make_silence(0.5).duration == pytest.approx(0.5, abs=1e-3)
    assert make_silence(0).duration == 0.0


def test_concat_adds_up():
    joined = concat([make_silence(0.2), make_silence(0.3)])
    assert joined.duration == pytest.approx(0.5, abs=1e-3)


def test_write_then_read_roundtrip(tmp_path):
    path = tmp_path / "x.wav"
    write_wav(make_silence(0.25), path)
    assert path.is_file()
    assert is_target_wav(path)


def test_is_target_wav_rejects_other_rate(tmp_path):
    assert not is_target_wav(make_wav(tmp_path / "a.wav", 0.1, rate=22050))


def test_extension_and_tool_status():
    assert extension_for("wav") == "wav"
    assert extension_for("m4a") == "m4a"
    assert missing_tool("wav") is None  # 零依赖
    formats = available_formats()
    assert set(formats) == {"wav", "m4a", "aac", "mp3"}
    assert formats["wav"] is True, "wav 不需要任何外部工具，必须是可用的"


def test_unknown_format_raises(tmp_path):
    with pytest.raises(ExportError):
        extension_for("flac")


# ---- 组装 -----------------------------------------------------------------

def test_assemble_wav_is_dependency_free(tmp_path):
    first = make_wav(tmp_path / "a.wav", 0.2)
    second = make_wav(tmp_path / "b.wav", 0.3)

    result = assemble([first, second], tmp_path / "out", fmt="wav", gap=0.1)

    assert result["format"] == "wav"
    assert result["segments"] == 2
    assert result["duration"] == pytest.approx(0.6, abs=0.02)
    assert Path(result["path"]).suffix == ".wav"
    assert Path(result["path"]).stat().st_size == result["bytes"]
    assert is_target_wav(Path(result["path"]))


def test_assemble_zero_gap_skips_silence(tmp_path):
    a = make_wav(tmp_path / "a.wav", 0.2)
    result = assemble([a, a], tmp_path / "out", fmt="wav", gap=0)
    assert result["duration"] == pytest.approx(0.4, abs=0.01)


def test_assemble_without_sources_raises(tmp_path):
    with pytest.raises(ExportError):
        assemble([], tmp_path / "out")


def test_assemble_unknown_format_rejected_early(tmp_path):
    with pytest.raises(ExportError):
        assemble([make_wav(tmp_path / "a.wav", 0.1)], tmp_path / "out", fmt="flac")


def test_assemble_cleans_up_workdir(tmp_path):
    a = make_wav(tmp_path / "a.wav", 0.1)
    assemble([a], tmp_path / "out", fmt="wav")
    assert not (tmp_path / ".nekopal-export-tmp").exists()


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="需要 macOS 自带的 afconvert")
def test_assemble_resamples_other_sample_rate(tmp_path):
    """say 后端给的是 22.05k，导出前要统一到 24k。"""

    odd = make_wav(tmp_path / "say.wav", 0.2, rate=22050)
    result = assemble([odd], tmp_path / "out", fmt="wav")
    assert is_target_wav(Path(result["path"]))
    assert result["duration"] == pytest.approx(0.2, abs=0.02)


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="需要 macOS 自带的 afconvert")
def test_assemble_m4a(tmp_path):
    a = make_wav(tmp_path / "a.wav", 0.3)
    result = assemble([a], tmp_path / "out", fmt="m4a")
    assert result["format"] == "m4a"
    assert Path(result["path"]).suffix == ".m4a"
    assert Path(result["path"]).stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is not None, reason="本机有 ffmpeg 时不测缺失分支")
def test_mp3_reports_missing_ffmpeg(tmp_path):
    with pytest.raises(ExportError) as excinfo:
        assemble([make_wav(tmp_path / "a.wav", 0.1)], tmp_path / "out", fmt="mp3")
    assert "ffmpeg" in str(excinfo.value)
