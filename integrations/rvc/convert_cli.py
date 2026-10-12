#!/usr/bin/env python
"""把 `infer_rvc_python` 包成一条命令行：**给一个 WAV，还一个 WAV**。

NekoPal 的变声后端（`nekopal convert`）只约定这一件事，所以本脚本就是那座桥。
它必须跑在**装了 RVC 的那个 venv** 里（见本目录 README）。

设计要点
--------
- **自己用 `soundfile` 读 WAV**，再以数组形式喂给 RVC。
  这样能**绕开 ffmpeg 依赖**（RVC 的 README 要求装 ffmpeg，但我们只需要 WAV，
  而 `soundfile` 处理 WAV 不需要 ffmpeg）。
- 默认 `only_cpu=True`：Mac 上没有 CUDA，让它在 CPU 上跑更稳；有 N 卡用 `--gpu`。
- 变调直接映射到 RVC 的 `pitch_lvl`（半音）。
- 任何失败都往 stderr 写**可读的原因**并以非零码退出 —— NekoPal 会把 stderr 原文展示给你。

用法::

    python convert_cli.py <输入.wav> <输出.wav> --model /path/rana.pth [--index /path/rana.index] [--pitch 0]
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

# ---------------------------------------------------------------------------
# ⚠️ 必须在 import torch / faiss **之前**设置，否则 macOS arm64 上会段错误：
#     libomp __kmp_create_worker → SIGSEGV（faiss 检索时创建 OpenMP 线程崩溃）
# 限制 OpenMP 线程数是关键；只允许重复库（KMP_DUPLICATE_LIB_OK）还不够。
# ---------------------------------------------------------------------------
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convert_cli.py", description="RVC 变声：给一个 WAV，还一个 WAV"
    )
    parser.add_argument("input", help="输入音频（WAV）")
    parser.add_argument("output", help="输出音频（WAV）")
    parser.add_argument("--model", required=True, help="RVC 模型 .pth")
    parser.add_argument("--index", default="", help="可选：.index 检索文件")
    parser.add_argument("--pitch", type=float, default=0.0, help="变调半音数（整数）")
    parser.add_argument(
        "--pitch-algo",
        default="pm",
        choices=["pm", "harvest", "crepe", "rmvpe", "rmvpe+"],
        help="音高提取算法；默认 pm（praat-parselmouth，不需要额外下载模型）",
    )
    parser.add_argument(
        "--index-influence", type=float, default=0.66, help="index 检索影响度 0~1"
    )
    parser.add_argument("--tag", default="nekopal", help="内部缓存标签，一般不用改")
    parser.add_argument("--gpu", action="store_true", help="有 NVIDIA 显卡时用（Mac 上别加）")
    parser.add_argument("--quiet", action="store_true", help="不打印进度")
    return parser


def fail(message: str) -> int:
    print("变声失败：{}".format(message), file=sys.stderr)
    return 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    warnings.filterwarnings("ignore")

    source = Path(args.input)
    target = Path(args.output)
    model = Path(args.model)

    if not source.is_file():
        return fail("输入音频不存在：{}".format(source))
    if not model.is_file():
        return fail(
            "模型不存在：{}\n"
            "（把你下载的 .pth 放到音色包目录里，或在 voice.yaml 里写对路径）".format(model)
        )
    index = Path(args.index) if args.index else None
    if index is not None and not index.is_file():
        return fail("index 文件不存在：{}".format(index))

    try:
        import soundfile as sf
    except ImportError:
        return fail("缺少 soundfile —— 这个脚本必须跑在装了 RVC 的 venv 里")

    try:
        from infer_rvc_python import BaseLoader
    except ImportError as exc:
        return fail(
            "import infer_rvc_python 失败：{}\n"
            "确认用的是装了 RVC 的那个解释器（见本目录 README）".format(exc)
        )

    try:
        data, sample_rate = sf.read(str(source), dtype="int16")
    except Exception as exc:  # noqa: BLE001
        return fail("读不了输入音频：{}".format(exc))

    if not args.quiet:
        print("输入 : {} ({:.2f}s @ {}Hz)".format(source.name, len(data) / sample_rate, sample_rate), file=sys.stderr)
        print("模型 : {}".format(model), file=sys.stderr)

    try:
        converter = BaseLoader(only_cpu=not args.gpu)
        converter.apply_conf(
            tag=args.tag,
            file_model=str(model),
            file_index=str(index) if index else "",
            pitch_algo=args.pitch_algo,
            pitch_lvl=int(round(args.pitch)),
            index_influence=args.index_influence,
        )
        # 传数组而不是路径：绕开 ffmpeg（README 里 audio_data 支持 (array, sr) 元组）
        result, out_rate = converter.generate_from_cache(
            audio_data=(data, sample_rate), tag=args.tag
        )
        converter.unload_models()
    except Exception as exc:  # noqa: BLE001
        return fail("{}: {}".format(type(exc).__name__, exc))

    if result is None:
        return fail("推理没有返回结果")

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(str(target), result, out_rate)
    except Exception as exc:  # noqa: BLE001
        return fail("写不了输出文件：{}".format(exc))

    if not args.quiet:
        print("输出 : {} ({:.2f}s @ {}Hz)".format(target, len(result) / out_rate, out_rate), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
