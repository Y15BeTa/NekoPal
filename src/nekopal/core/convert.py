"""变声（音频 → 音频）扩展点。

RVC / DDSP-SVC 这类模型做的是**声音转换**：输入一段人声，输出同内容的另一种音色。
它们**不是 TTS，不能直接喂文本**，所以与 `TTSBackend`（文本 → 音频）分开成为一层。

设计取舍
--------
本项目**不内置任何模型、也不猜任何工具的 API**。
变声怎么跑完全由**你配置的命令**决定 —— 模板里用占位符：

    {input}      合成出来的原始音频（WAV，24k 单声道）
    {output}     变声结果要写到哪里（也是 WAV）
    {model}      音色包里声明的模型路径
    {index}      音色包里声明的 index 路径（可为空串）
    {pitch}      变调半音数（来自 options.pitch）

例：

    python infer_cli.py {input} {output} --model {model} --index {index} --pitch {pitch}

这样 RVC、DDSP-SVC、你自己写的包装脚本、甚至 docker 命令都能接 —— 我们只约定
"给它一个 WAV，还我一个 WAV"。
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Sequence, runtime_checkable

#: 模板里允许出现的占位符
PLACEHOLDERS = ("input", "output", "model", "index", "pitch")


class ConvertError(RuntimeError):
    """变声失败（命令不存在、超时、没有产出文件等）。"""


@dataclass(frozen=True)
class ConvertRequest:
    input_path: Path
    output_path: Path
    model: Optional[str] = None
    index: Optional[str] = None
    pitch: float = 0.0


@runtime_checkable
class VoiceConverter(Protocol):
    id: str

    def available(self) -> bool:
        ...

    async def convert(self, request: ConvertRequest) -> Path:
        ...


class CommandConverter:
    """按模板执行一条本地命令完成变声 —— 最通用，能接任何工具。"""

    id = "command"

    def __init__(self, template: Sequence[str], timeout: float = 600.0) -> None:
        self.template: List[str] = [str(part) for part in template]
        self.timeout = timeout

    # -- 状态 ------------------------------------------------------------
    def available(self) -> bool:
        return bool(self.template)

    def describe(self) -> str:
        return " ".join(self.template) if self.template else "（未配置）"

    # -- 渲染与执行 ------------------------------------------------------
    def render(self, request: ConvertRequest) -> List[str]:
        mapping = {
            "input": str(request.input_path),
            "output": str(request.output_path),
            "model": request.model or "",
            "index": request.index or "",
            "pitch": str(int(round(request.pitch))),
        }
        try:
            return [part.format(**mapping) for part in self.template]
        except KeyError as exc:
            raise ConvertError(
                "命令模板里有不认识的占位符 {}；可用的是 {}".format(
                    exc, "、".join("{" + name + "}" for name in PLACEHOLDERS)
                )
            ) from exc

    async def convert(self, request: ConvertRequest) -> Path:
        if not self.available():
            raise ConvertError(
                "还没有配置变声命令。设置环境变量 NEKOPAL_RVC_COMMAND，"
                "或在音色包里写 `rvc.command`。"
            )

        command = self.render(request)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        if request.output_path.exists():
            request.output_path.unlink()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._run_sync, command, request)

        if not request.output_path.is_file():
            raise ConvertError(
                "命令执行完了，但没有产出 {}\n命令：{}".format(
                    request.output_path, " ".join(command)
                )
            )
        return request.output_path

    def _run_sync(self, command: Sequence[str], request: ConvertRequest) -> None:
        if not request.input_path.is_file():
            raise ConvertError("输入音频不存在：{}".format(request.input_path))
        try:
            completed = subprocess.run(
                list(command), capture_output=True, timeout=self.timeout
            )
        except FileNotFoundError as exc:
            raise ConvertError(
                "找不到命令 `{}` —— 检查 NEKOPAL_RVC_COMMAND 的第一段是不是可执行程序".format(
                    command[0]
                )
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ConvertError("变声超时（{} 秒没跑完）".format(self.timeout)) from exc

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            raise ConvertError(
                "变声命令退出码 {}：{}".format(
                    completed.returncode, stderr[-400:] or "（没有 stderr 输出）"
                )
            )
