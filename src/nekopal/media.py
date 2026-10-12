"""音频小工具（不依赖 ffmpeg）。"""

from __future__ import annotations

#: Edge-TTS 默认输出 audio-24khz-48kbitrate-mono-mp3 ≈ 6000 字节/秒
MP3_BYTES_PER_SECOND = 6000.0


def wav_duration(data: bytes) -> float:
    """从 WAV 字节中解析时长（秒）。解析失败返回 0.0。"""

    if len(data) < 44 or data[:4] != b"RIFF":
        return 0.0

    pos = 12
    fmt = None
    data_size = None
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = pos + 8
        if chunk_id == b"fmt " and body + 16 <= len(data):
            channels = int.from_bytes(data[body + 2 : body + 4], "little")
            sample_rate = int.from_bytes(data[body + 4 : body + 8], "little")
            bits = int.from_bytes(data[body + 14 : body + 16], "little")
            fmt = (channels, sample_rate, bits)
        elif chunk_id == b"data":
            data_size = min(size, max(0, len(data) - body))
            break
        pos = body + size + (size & 1)

    if fmt is None or data_size is None:
        return 0.0
    channels, sample_rate, bits = fmt
    if sample_rate <= 0 or bits <= 0 or channels <= 0:
        return 0.0
    return data_size / float(sample_rate * channels * (bits // 8))
