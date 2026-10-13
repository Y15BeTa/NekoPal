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
from dataclasses import replace
from pathlib import Path
from typing import Callable, List, Optional

from nekopal.core.convert import (
    CommandConverter,
    ConvertError,
    ConvertRequest,
    VoiceConverter,
)
from nekopal.core.export import decode, is_target_wav, write_wav
from nekopal.core.models import (
    Capabilities,
    SynthOptions,
    SynthResult,
    Voice,
)
from nekopal.media import wav_duration, wav_sample_rate


class RvcBackend:
    id = "rvc"
    name = "变声（RVC / DDSP-SVC 等）"

    def __init__(self, settings, converter: Optional[VoiceConverter] = None) -> None:
        self._settings = settings
        self._converter = converter
        self._registry = None
        self._lookup: Optional[Callable[[str], object]] = None
        self._pack_source: Optional[Callable[[], object]] = None

    # -- 装配 ------------------------------------------------------------
    def attach(self, registry, lookup: Callable[[str], object], pack_source=None) -> None:
        """由 Runtime 在装配时调用：拿到注册表、音色包查询函数、以及音色包总表。

        ``pack_source`` 用来回答"这个后端到底能不能用" —— 变声命令既可以来自全局
        环境变量，也可以写在**音色包内部**（设计文档 6.9）。只看全局变量的话，
        把命令写在包里的用户会被误判成"后端不可用"（踩过：会被静默换成系统音色）。
        """

        self._registry = registry
        self._lookup = lookup
        self._pack_source = pack_source

    # -- 能力 ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            # 变声本身**不产生**时间戳，但会**透传** base 音色的（见 synth 里的
            # `words=list(base_result.words)`），所以这里报 True —— 否则
            # /api/voices 与 /api/backends 会把这个音色标成"没有词级能力"，
            # 按能力协商的客户端会白白关掉逐词高亮（又是一处"能力位撒谎"）。
            # ⚠️ 若 base 是 `say` 这类没有时间戳的后端，返回的 `words` 会是空数组 ——
            #    客户端必须容忍空 words（这是任何 TTS 的通用要求）。
            word_timings=True,
            rate=True,            # 由 base_voice 负责
            pitch=True,           # 映射到命令模板的 {pitch}
            volume=False,
            streaming=False,
            offline=True,
        )

    def available(self) -> bool:
        return self.converter() is not None or self._any_pack_command()

    def _any_pack_command(self) -> bool:
        """有没有哪个音色包自带变声命令（``rvc.command``）。"""

        if self._pack_source is None:
            return False
        try:
            collection = self._pack_source()
        except Exception:  # noqa: BLE001 - 可用性判断不该因扫描出错而崩
            return False
        for pack in getattr(collection, "voices", None) or []:
            if getattr(pack, "command", None):
                return True
        return False

    def converter(self) -> Optional[VoiceConverter]:
        if self._converter is not None and self._converter.available():
            return self._converter
        template = getattr(self._settings, "rvc_command", None)
        if template:
            # 必须把 rvc_timeout 带上 —— 否则服务端路径恒用默认 600 秒，
            # 用户设的 NEKOPAL_RVC_TIMEOUT 只在 CLI 里生效，看起来"设了没用"。
            self._converter = CommandConverter(
                template,
                timeout=float(getattr(self._settings, "rvc_timeout", 600.0)),
            )
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
        #    音色包可以用 `rvc.rate` 调 **base 的语速** —— 这是「慵懒」的主要旋钮：
        #    RVC 保留时间轴，所以源慢下来，最终输出也跟着慢（语气就松下来了）。
        #
        # ⚠️ **基础音必须把 pitch 清成 0**（2026-10-14 修）：
        #    变调应该由**变声那一级**独家负责（RVC 的 `--pitch` 单位是半音）；
        #    而基础音后端（edge）把 pitch 当 **Hz** 用（`+pitch×50Hz`，非常猛），
        #    两边一叠加 —— 界面上调 1 格，实测跳了 **6~14 个半音**（试听 8 那份量出来的）。
        #    所以这里只保留语速，pitch 一律归 0。
        base_options = options
        pack_rate = getattr(pack, "rvc_rate", None)
        want_rate = float(pack_rate) if pack_rate else options.rate
        if abs(want_rate - options.rate) > 1e-3 or abs(options.pitch) > 1e-3:
            base_options = replace(options, rate=want_rate, pitch=0.0)
        base_result = await self._registry.synth(base_voice, text, base_options)

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
            # 采样率由变声工具决定（RVC 常见 40k/48k），从 WAV 头读，不要写死。
            sample_rate=wav_sample_rate(data),
            duration=wav_duration(data),
            # 变声本身不产生时间戳，但**它几乎不改动时间轴**（实测 5.1s 音频漂移约 16ms），
            # 所以把 base 音色的词级时间戳**透传**上去 —— 前端有词就逐词高亮，没有就退回句级。
            words=list(base_result.words),
            media_type="audio/wav",
            extension="wav",
        )

    async def aclose(self) -> None:
        return None
