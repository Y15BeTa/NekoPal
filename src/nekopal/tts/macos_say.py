"""macOS ``say`` 后端（离线兜底，设计文档 4.7 的默认音色来源）。

- 离线、零依赖、绝对合法 → 默认音色链的首选
- 不支持词级时间戳（前端自动降级为句级高亮）
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from nekopal.core.models import Capabilities, SynthOptions, SynthResult, Voice
from nekopal.media import wav_duration

SAY_BIN = "/usr/bin/say"
SAMPLE_RATE = 22050
BASE_WPM = 175  # say 默认语速

_VOICE_RE = re.compile(
    r"^(?P<name>.*?)\s+(?P<locale>[a-z]{2}_[A-Z]{2})\s+#\s*(?P<sample>.*)$"
)

#: 优先使用的中文音色名（不同 macOS 版本命名不同，命中即用）
_PREFERRED_NAMES = ("Tingting", "婷婷", "Meijia", "Sinji", "Li-mu", "Yu-shu", "Panpan")


class SayBackend:
    id = "say"
    name = "macOS say"

    def __init__(self) -> None:
        self._voices: Optional[List[Voice]] = None

    # -- 能力 ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            word_timings=False,
            rate=True,
            pitch=False,
            volume=False,
            streaming=False,
            offline=True,
        )

    def available(self) -> bool:
        return Path(SAY_BIN).exists()

    # -- 音色 ------------------------------------------------------------
    def _load_voices(self) -> List[Voice]:
        if self._voices is not None:
            return self._voices

        voices: List[Voice] = []
        output = ""
        if self.available():
            try:
                proc = subprocess.run(
                    [SAY_BIN, "-v", "?"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                output = proc.stdout or ""
            except (OSError, subprocess.SubprocessError):
                output = ""

        for line in output.splitlines():
            match = _VOICE_RE.match(line.rstrip())
            if not match:
                continue
            name = match.group("name").strip()
            locale = match.group("locale")
            if not name:
                continue
            voices.append(
                Voice(
                    id="say:{}".format(name),
                    name=name,
                    backend=self.id,
                    lang=[locale.split("_")[0]],
                    tags=[locale],
                    capabilities=self.capabilities(),
                    license="official",
                    commercial_ok=True,
                    redistributable=True,
                )
            )

        self._voices = voices
        return voices

    def default_voice_id(self) -> str:
        voices = self._load_voices()
        if not voices:
            return "say:auto"
        for wanted in _PREFERRED_NAMES:
            for voice in voices:
                if voice.name == wanted:
                    return voice.id
        for voice in voices:
            if "zh_CN" in voice.tags:
                return voice.id
        for voice in voices:
            if voice.lang and voice.lang[0] == "zh":
                return voice.id
        return voices[0].id

    async def list_voices(self) -> List[Voice]:
        return list(self._load_voices())

    def preload(self) -> None:
        """同步预热（供启动时在线程里调用）。"""

        self._load_voices()

    # -- 合成 ------------------------------------------------------------
    def _resolve_local(self, voice_id: str) -> str:
        local = voice_id.split(":", 1)[1] if ":" in voice_id else voice_id
        if local in ("", "auto"):
            local = self.default_voice_id().split(":", 1)[1]
        return local

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        local = self._resolve_local(voice_id)
        wpm = max(80, int(round(BASE_WPM * (options.rate or 1.0))))
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._synth_sync, text, local, wpm)

    def _synth_sync(self, text: str, voice_name: str, wpm: int) -> SynthResult:
        if not self.available():
            raise RuntimeError("`say` 不可用：这是 Windows/Linux 环境？")

        with tempfile.TemporaryDirectory(prefix="nekopal-say-") as tmpdir:
            txt_path = Path(tmpdir) / "in.txt"
            wav_path = Path(tmpdir) / "out.wav"
            # 用 -f 从文件读文本：避免超长文本撞上命令行参数上限
            txt_path.write_text(text, encoding="utf-8")
            cmd = [
                SAY_BIN,
                "-f",
                str(txt_path),
                "-o",
                str(wav_path),
                "--data-format=LEI16@{}".format(SAMPLE_RATE),
                "-r",
                str(wpm),
            ]
            if voice_name:
                cmd += ["-v", voice_name]

            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=180)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("say 合成超时") from exc

            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", "replace").strip()
                raise RuntimeError("say 失败: {}".format(stderr or proc.returncode))
            if not wav_path.is_file():
                raise RuntimeError("say 没有产生音频输出")

            data = wav_path.read_bytes()

        return SynthResult(
            audio=data,
            sample_rate=SAMPLE_RATE,
            duration=wav_duration(data),
            words=[],
            media_type="audio/wav",
            extension="wav",
        )

    async def aclose(self) -> None:
        return None


def is_say_available() -> bool:
    return Path(SAY_BIN).exists() or shutil.which("say") is not None
