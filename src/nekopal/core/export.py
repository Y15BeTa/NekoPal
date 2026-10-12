"""把已合成的音频段拼成一个可带走的文件（设计文档 5.8「通勤播客」）。

**零额外依赖的秘诀**：macOS 自带 AudioToolbox 的 ``afconvert``，既能解码 mp3，
又能编 AAC/m4a。因此：

===========  ================  ==========================================
格式          依赖               说明
===========  ================  ==========================================
``wav``      ✅ 零依赖           纯 Python 拼接，最快
``m4a``      ✅ ``afconvert``   体积小、通用，**通勤播客的推荐默认**
``aac``      ✅ ``afconvert``
``mp3``      ⚠️ 需 ``ffmpeg``    有就用，没有就明确报错并给安装提示
===========  ================  ==========================================

本模块只做「路径进 → 文件出」，不认识任务与网络；
调用方负责先把各段音频准备好（复用缓存，不重复合成）。
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: 统一成这个格式再拼接（不同后端给的采样率不一样：say 22.05k、edge 24k）
TARGET_RATE = 24000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # 16-bit

#: 目标格式 → (扩展名, 需要的命令行工具)
_FORMATS: Dict[str, tuple] = {
    "wav": ("wav", None),
    "m4a": ("m4a", "afconvert"),
    "aac": ("aac", "afconvert"),
    "mp3": ("mp3", "ffmpeg"),
}

AAC_BITRATE = "64000"


class ExportError(RuntimeError):
    """导出失败（缺工具、源文件不可解码等）。"""


# --------------------------------------------------------------------------
# 工具与格式
# --------------------------------------------------------------------------

def tool_path(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return shutil.which(name)


def available_formats() -> Dict[str, bool]:
    """各格式在本机是否可用。

    注意 ``wav`` 不需要任何外部工具（tool 为 None），别把它算成不可用。
    """

    return {
        fmt: (tool is None) or (tool_path(tool) is not None)
        for fmt, (_, tool) in _FORMATS.items()
    }


def extension_for(fmt: str) -> str:
    if fmt not in _FORMATS:
        raise ExportError(
            "不支持的格式 {}（可选：{}）".format(fmt, "、".join(_FORMATS))
        )
    return _FORMATS[fmt][0]


def missing_tool(fmt: str) -> Optional[str]:
    tool = _FORMATS.get(fmt, (None, None))[1]
    if tool and tool_path(tool) is None:
        return tool
    return None


def _run(cmd: Sequence[str], what: str) -> None:
    try:
        proc = subprocess.run(list(cmd), capture_output=True, timeout=1800)
    except FileNotFoundError as exc:
        raise ExportError("{}失败：找不到命令 {}".format(what, cmd[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise ExportError("{}超时".format(what)) from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise ExportError("{}失败：{}".format(what, stderr or proc.returncode))


# --------------------------------------------------------------------------
# PCM
# --------------------------------------------------------------------------

@dataclass
class Pcm:
    data: bytes
    sample_rate: int = TARGET_RATE
    channels: int = TARGET_CHANNELS
    sample_width: int = TARGET_SAMPLE_WIDTH

    @property
    def duration(self) -> float:
        divisor = self.sample_rate * self.channels * self.sample_width
        return len(self.data) / float(divisor) if divisor else 0.0


def is_target_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            return (
                handle.getsampwidth() == TARGET_SAMPLE_WIDTH
                and handle.getframerate() == TARGET_RATE
                and handle.getnchannels() == TARGET_CHANNELS
            )
    except (wave.Error, OSError, EOFError):
        return False


def read_target_wav(path: Path) -> Pcm:
    with wave.open(str(path), "rb") as handle:
        return Pcm(
            data=handle.readframes(handle.getnframes()),
            sample_rate=handle.getframerate(),
            channels=handle.getnchannels(),
            sample_width=handle.getsampwidth(),
        )


def make_silence(seconds: float) -> Pcm:
    frames = max(0, int(TARGET_RATE * seconds))
    return Pcm(data=b"\x00" * (frames * TARGET_SAMPLE_WIDTH * TARGET_CHANNELS))


def concat(pieces: Sequence[Pcm]) -> Pcm:
    return Pcm(data=b"".join(piece.data for piece in pieces))


def write_wav(pcm: Pcm, path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(pcm.channels)
        handle.setsampwidth(pcm.sample_width)
        handle.setframerate(pcm.sample_rate)
        handle.writeframes(pcm.data)


def decode(path: Path, workdir: Path) -> Pcm:
    """把任意受支持的音频解成统一 PCM（24k / 单声道 / 16bit）。"""

    if is_target_wav(path):
        return read_target_wav(path)

    afconvert = tool_path("afconvert")
    if afconvert is None:
        raise ExportError(
            "无法解码 {}：需要 afconvert（macOS 自带）或 ffmpeg".format(path.name)
        )

    decoded = workdir / (path.stem + ".decoded.wav")
    _run(
        [
            afconvert,
            "-f", "WAVE",
            "-d", "LEI16@{}".format(TARGET_RATE),
            "-c", str(TARGET_CHANNELS),
            str(path),
            str(decoded),
        ],
        "解码 {}".format(path.name),
    )
    if not is_target_wav(decoded):
        raise ExportError("解码结果不是预期的 WAV 格式：{}".format(path.name))
    return read_target_wav(decoded)


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------

def assemble(
    sources: Sequence[Path],
    output_base: Path,
    fmt: str = "m4a",
    gap: float = 0.15,
    workdir: Optional[Path] = None,
) -> dict:
    """把若干音频文件拼成一个文件。

    ``output_base`` 是**不含扩展名**的目标路径。
    """

    if not sources:
        raise ExportError("没有可导出的音频段")

    extension = extension_for(fmt)  # 提前校验格式，别等解码完才发现不支持

    tool = missing_tool(fmt)
    if tool is not None:
        hint = "brew install ffmpeg" if tool == "ffmpeg" else "该功能需要 macOS 自带的 {}".format(tool)
        raise ExportError("导出 {} 需要 {}：{}".format(fmt, tool, hint))

    own_workdir = workdir is None
    workdir = workdir or Path(output_base).parent / ".nekopal-export-tmp"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        pieces: List[Pcm] = []
        for index, source in enumerate(sources):
            if index > 0 and gap > 0:
                pieces.append(make_silence(gap))
            pieces.append(decode(Path(source), workdir))

        merged = concat(pieces)
        merged_wav = workdir / "merged.wav"
        write_wav(merged, merged_wav)

        output = Path(str(output_base) + "." + extension)
        output.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "wav":
            shutil.move(str(merged_wav), str(output))
        elif fmt in ("m4a", "aac"):
            file_format = "m4af" if fmt == "m4a" else "adts"
            _run(
                [
                    tool_path("afconvert"),
                    "-f", file_format,
                    "-d", "aac",
                    "-b", AAC_BITRATE,
                    str(merged_wav),
                    str(output),
                ],
                "编码 {}".format(fmt),
            )
        else:  # mp3
            _run(
                [
                    tool_path("ffmpeg"),
                    "-y",
                    "-i", str(merged_wav),
                    "-b:a", "64k",
                    str(output),
                ],
                "编码 mp3",
            )

        return {
            "path": str(output),
            "format": fmt,
            "duration": round(merged.duration, 3),
            "segments": len(sources),
            "bytes": output.stat().st_size,
        }
    finally:
        if own_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
