"""音频小工具（不依赖 ffmpeg）。"""

from __future__ import annotations

from typing import Iterator, Optional, Tuple

#: Edge-TTS 默认输出 audio-24khz-48kbitrate-mono-mp3 ≈ 6000 字节/秒
MP3_BYTES_PER_SECOND = 6000.0

#: 声明 WAV 时的回退采样率（解析失败时才用；正常 WAV 都能解析出来）
FALLBACK_SAMPLE_RATE = 24000


def _wav_chunks(data: bytes) -> Iterator[Tuple[bytes, int, int]]:
    """遍历 WAV 的 chunk，产出 ``(chunk_id, body_offset, size)``。

    不是 WAV（或太短）时直接产出空序列。
    """

    if len(data) < 44 or data[:4] != b"RIFF":
        return

    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        yield chunk_id, pos + 8, size
        pos = pos + 8 + size + (size & 1)  # chunk 之间按偶数对齐


def _wav_format(data: bytes) -> Optional[Tuple[int, int, int]]:
    """读出 WAV 的 ``(声道数, 采样率, 位深)``；不可用时返回 ``None``。"""

    for chunk_id, body, _ in _wav_chunks(data):
        if chunk_id == b"fmt " and body + 16 <= len(data):
            channels = int.from_bytes(data[body + 2 : body + 4], "little")
            sample_rate = int.from_bytes(data[body + 4 : body + 8], "little")
            bits = int.from_bytes(data[body + 14 : body + 16], "little")
            if channels > 0 and sample_rate > 0 and bits > 0:
                return channels, sample_rate, bits
            return None
    return None


def wav_sample_rate(data: bytes) -> int:
    """从 WAV 字节里读出采样率（Hz）。解析失败返回 :data:`FALLBACK_SAMPLE_RATE`。

    为什么需要它：变声（RVC 等）输出的采样率**由模型/工具决定**（40k / 48k 都常见），
    写死一个值会让元数据撒谎。
    """

    fmt = _wav_format(data)
    return fmt[1] if fmt else FALLBACK_SAMPLE_RATE


def wav_duration(data: bytes) -> float:
    """从 WAV 字节中解析时长（秒）。解析失败返回 0.0。"""

    fmt = _wav_format(data)
    if fmt is None:
        return 0.0
    channels, sample_rate, bits = fmt

    for chunk_id, body, size in _wav_chunks(data):
        if chunk_id == b"data":
            data_size = min(size, max(0, len(data) - body))
            return data_size / float(sample_rate * channels * (bits // 8))
    return 0.0
