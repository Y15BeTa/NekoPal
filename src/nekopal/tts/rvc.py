"""变声后端：先合成，再跑你配置的变声命令。

RVC / DDSP-SVC 这类模型是**音频 → 音频**（声音转换），不能直接喂文本。
所以本后端是一条**组合管线**：

    文本 --(base_voice 合成)--> WAV --(你的变声命令)--> 目标音色 WAV

音色包写法（放在 `~/.config/nekopal/voices/<id>/voice.yaml`）::

    id: my-converted
    name: "我的变声音色"
    backend: rvc
    license: unofficial            # 未获公开授权的模型如实标 unofficial
    commercial_ok: false
    redistributable: false

    rvc:
      base_voice: edge:zh-CN-XiaoxiaoNeural   # 先用谁合成（必填）
      model: /abs/path/voice.pth               # 必填
      index: /abs/path/voice.index             # 可选
      command: ["python", "infer.py", "{input}", "{output}", "--model", "{model}"]  # 可选，覆盖全局

**本项目不内置模型、也不猜任何工具的 API**：变声怎么跑由命令模板决定
（见 `core/convert.py`）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, List, Optional

from nekopal.core.convert import CommandConverter, ConvertError, ConvertRequest, VoiceConverter
from nekopal.core.export import decode, is_target_wav, write_wav
from nekopal.core.models import Capabilities, SynthOptions, SynthResult, Voice, WordTiming
from nekopal.media import wav_duration


class RvcBackend:
    id = "rvc"
    name = "变声（RVC / DDSP-SVC 等）"

    def __init__(self, settings, converter: Optional[VoiceConverter] = None) -> None:
        self._settings = settings
        self._converter = converter
        self._registry = None
        self._lookup: Optional[Callable[[str], object]] = None

    # -- 装配 ------------------------------------------------------------
    def attach(self, registry, lookup: Callable[[str], object]) -> None:
        """由 Registry / Runtime 在装配时调用：拿到注册表与音色包查询函数。"""

        self._registry = registry
        self._lookup = lookup

    # -- 能力 ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            word_timings=False,   # 变声不产生词级时间戳（前端自动降级为句级）
            rate=True,            # 由 base_voice 负责
            pitch=True,           # 映射到命令模板的 {pitch}
            volume=False,
            streaming=False,
            offline=True,
        )

    def available(self) -> bool:
        return self.converter() is not None

    def converter(self) -> Optional[VoiceConverter]:
        if self._converter is not None and self._converter.available():
            return self._converter
        template = getattr(self._settings, "rvc_command", None)
        if template:
            self._converter = CommandConverter(template)
            return self._converter
        return None

    # -- 音色 ------------------------------------------------------------
    async def list_voices(self) -> List[Voice]:
        # 变声音色全部来自**用户自己的音色包**，由 /api/voices 合并时提供
        return []

    # -- 合成 ------------------------------------------------------------
    def _pack(self, voice_id: str):
        if self._lookup is None:
            return None
        return self._lookup(voice_id)

    @staticmethod
    def _prepare_input(result: SynthResult, workdir: Path) -> Path:
        """把基础音色产出的音频变成变声工具能吃的 WAV（24k / 单声道 / 16bit）。"""

        raw = workdir / ("base." + result.extension)
        raw.write_bytes(result.audio)
        if is_target_wav(raw):
            return raw
        target = workdir / "base.wav"
        write_wav(decode(raw, workdir), target)
        return target

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        pack = self._pack(voice_id)
        if pack is None:
            raise RuntimeError(
                "找不到 {} 对应的音色包定义（放 ~/.config/nekopal/voices/ 下）".format(voice_id)
            )

        base_voice = getattr(pack, "base_voice", None)
        if not base_voice:
            raise RuntimeError("音色包 `{}` 没有写 `rvc.base_voice`".format(pack.id))
        if self._registry is None:
            raise RuntimeError("rvc 后端尚未绑定注册表")

        # ① 先用基础音色把文本读出来
        base_result = await self._registry.synth(base_voice, text, options)

        # ② 再跑变声
        own_command = getattr(pack, "command", None)
        converter = CommandConverter(own_command) if own_command else self.converter()
        if converter is None:
            raise ConvertError(
                "还没有配置变声命令。设置 `NEKOPAL_RVC_COMMAND`，或在音色包的 `rvc.command` 里写。"
            )

        with tempfile.TemporaryDirectory(prefix="nekopal-rvc-") as tmp:
            workdir = Path(tmp)
            source = self._prepare_input(base_result, workdir)
            target = workdir / "converted.wav"

            await converter.convert(
                ConvertRequest(
                    input_path=source,
                    output_path=target,
                    model=getattr(pack, "rvc_model", None),
                    index=getattr(pack, "rvc_index", None),
                    pitch=options.pitch,
                )
            )
            data = target.read_bytes()

        return SynthResult(
            audio=data,
            sample_rate=24000,
            duration=wav_duration(data),
            words=[],  # 变声不提供词级时间戳
            media_type="audio/wav",
            extension="wav",
        )

    async def aclose(self) -> None:
        return None
