#!/usr/bin/env python
"""常驻 GPT-SoVITS 服务：模型只加载一次，之后每句只走一次 HTTP。

**为什么需要它**：GPT-SoVITS 的 GPT 权重 148 MB + SoVITS 权重 165 MB，
每句都新起进程重读一遍是没法用的。本服务把引擎常驻内存，换音色时才切权重。

用法::

    # 启动（跑在装了 GPT-SoVITS 的那个 venv 里）
    ~/.local/share/nekopal/sovits-venv/bin/python integrations/sovits/serve.py

    # NekoPal 默认就找 127.0.0.1:9880；换个地方才需要设
    export NEKOPAL_SOVITS_ENDPOINT=http://127.0.0.1:9880

它和 `integrations/rvc/serve.py` 是一对：
  rvc    = 把"已有的朗读声"变成目标音色（变声，语气来自 base）
  sovits = 直接"文字 → 目标音色"（端到端，语气来自权重 + 参考音频）

**本项目只做接口，不分发模型。** 引擎从哪来、权重合不合法，由你自己准备；
音色包里的 `license` / `commercial_ok` / `redistributable` 要如实填写。

⚠️ 这个脚本**跑在 GPT-SoVITS 的 venv 里**，不在 NekoPal 的 venv 里 ——
它 `import` 的是 GPT-SoVITS 的代码，而 NekoPal 的依赖里没有 torch。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ⚠️ 都必须在 import torch 之前设
#   · OpenMP 线程数：macOS arm64 上开太多容易崩（RVC 那边踩过 faiss 的同类坑）
#   · torch 2.6+ 改了 weights_only 默认值，GPT-SoVITS 的旧 ckpt 会被拒
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

DEFAULT_ENGINE = Path.home() / ".local/share/nekopal/sovits"

_LOCK = threading.Lock()
_STATE: dict = {
    "engine": None,
    "config_key": None,
    "gpt": None,      #: 当前引擎里加载的 GPT 权重
    "sovits": None,   #: 当前引擎里加载的 SoVITS 权重
    "chunks": 0,
    "seconds": 0.0,
}
_STARTED_AT = time.time()


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------

def _setup_paths(engine_dir: Path) -> None:
    """让 `import TTS_infer_pack` / `import sv` 能找到（GPT-SoVITS 假设自己在 CWD）。"""

    for path in (engine_dir, engine_dir / "GPT_SoVITS", engine_dir / "GPT_SoVITS/eres2net"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def ensure_engine(engine_dir: Path, device: str, version: str, is_half: bool):
    """保证内存里的引擎就是这个配置；配置变了才重建（重建很贵）。"""

    key = "{}|{}|{}|{}".format(engine_dir, device, version, is_half)
    if _STATE["engine"] is not None and _STATE["config_key"] == key:
        return _STATE["engine"]

    # ⚠️ **必须把工作目录切到引擎根目录** ✗
    # GPT-SoVITS 的 `TTS_Config.default_configs` 里那些权重路径**是相对路径** ✗
    # （如 `GPT_SoVITS/pretrained_models/s1v3.ckpt` ✗），它是按 **CWD** 去找的 ✗。
    # 不在引擎目录下启动就会报：
    #     FileNotFoundError: 'GPT_SoVITS/pretrained_models/s1v3.ckpt'
    # （踩过 ✗ —— 而那个文件明明在 ✗，让人以为是模型没下全 ✗）。
    # 官方 `webui.py` 也是在仓库根目录跑的 ✗。顺带 `__init__` 里的
    # `os.makedirs("GPT_SoVITS/configs/")` 也会落在该落的地方 ✗。
    # 传进来的 `ref_audio` / 权重路径都是**绝对路径** ✗，所以切目录不影响它们 ✗。
    os.chdir(engine_dir)

    _setup_paths(engine_dir)

    pretrained = engine_dir / "GPT_SoVITS/pretrained_models"
    bert = pretrained / "chinese-roberta-wwm-ext-large"
    hubert = pretrained / "chinese-hubert-base"
    eres = pretrained / "sv/pretrained_eres2netv2w24s4ep4.ckpt"
    for path, label in ((bert, "chinese-roberta-wwm-ext-large"), (hubert, "chinese-hubert-base"),
                        (eres, "sv/pretrained_eres2netv2w24s4ep4.ckpt")):
        if not path.exists():
            raise FileNotFoundError(
                "缺少基础模型：{}（跑 tools/download_models.sh 或按 integrations/sovits/README.md 下载）".format(label)
            )

    # 这两处必须**在 import 之前**指好 —— 否则 GPT-SoVITS 会按 CWD 去找
    import sv  # noqa: E402

    sv.sv_path = str(eres)

    import text.g2pw as g2pw  # noqa: E402

    original_init = g2pw.G2PWPinyin.__init__

    def local_g2pw(self, *args, **kwargs):
        kwargs.update(model_dir=str(engine_dir / "GPT_SoVITS/text/G2PWModel"), model_source=str(bert))
        original_init(self, *args, **kwargs)

    g2pw.G2PWPinyin.__init__ = local_g2pw

    from TTS_infer_pack.TTS import TTS, TTS_Config  # noqa: E402

    config = TTS_Config(
        {
            "custom": dict(
                device=device,
                is_half=is_half,
                version=version,
                bert_base_path=str(bert),
                cnhuhbert_base_path=str(hubert),
            )
        }
    )
    # 让它把推理用的临时配置写到一个**可写**的地方（默认可能落在只读目录）
    runtime = Path(os.environ.get("NEKOPAL_SOVITS_RUNTIME") or (Path.home() / ".cache/nekopal/sovits"))
    runtime.mkdir(parents=True, exist_ok=True)
    config.configs_path = str(runtime / "inference_config.yaml")

    started = time.time()
    engine = TTS(config)
    _STATE["engine"] = engine
    _STATE["config_key"] = key
    _STATE["gpt"] = None
    _STATE["sovits"] = None
    print("[sovits] 引擎已加载（{:.1f}s · {} · {}）".format(time.time() - started, device, version), flush=True)
    return engine


def _switch_weights(engine, gpt: str, sovits: str) -> None:
    """只切**变了**的那一个 —— 每次切都要重读上百 MB。"""

    if gpt and _STATE["gpt"] != gpt:
        started = time.time()
        engine.init_t2s_weights(gpt)
        _STATE["gpt"] = gpt
        print("[sovits] 切 GPT 权重（{:.1f}s）：{}".format(time.time() - started, Path(gpt).name), flush=True)
    if sovits and _STATE["sovits"] != sovits:
        started = time.time()
        engine.init_vits_weights(sovits)
        _STATE["sovits"] = sovits
        print("[sovits] 切 SoVITS 权重（{:.1f}s）：{}".format(time.time() - started, Path(sovits).name), flush=True)


# --------------------------------------------------------------------------
# 合成
# --------------------------------------------------------------------------

def do_tts(payload: dict) -> bytes:
    import numpy as np
    import soundfile as sf

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    ref_audio = str(payload.get("ref_audio") or "")
    if not ref_audio or not Path(ref_audio).is_file():
        raise ValueError("参考音频不存在：{}".format(ref_audio))

    prompt_text = str(payload.get("prompt_text") or "").strip()
    if not prompt_text:
        raise ValueError("prompt_text 不能为空（参考音频的逐字文本）")

    prompt_lang = str(payload.get("prompt_lang") or "zh")
    text_lang = str(payload.get("text_lang") or prompt_lang)
    speed = float(payload.get("speed") or 1.0)
    seed = int(payload.get("seed", -1))
    split = str(payload.get("text_split_method") or "cut0")

    engine_dir = Path(str(payload.get("engine_dir") or DEFAULT_ENGINE)).expanduser()
    gpt = str(payload.get("gpt_weights") or "")
    sovits = str(payload.get("sovits_weights") or "")
    for path, label in ((gpt, "GPT 权重"), (sovits, "SoVITS 权重")):
        if path and not Path(path).is_file():
            raise ValueError("{}不存在：{}".format(label, path))

    request = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": ref_audio,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang,
        "top_k": int(payload.get("top_k") or 15),
        "top_p": float(payload.get("top_p") or 1.0),
        "temperature": float(payload.get("temperature") or 1.0),
        "text_split_method": split,
        "batch_size": 1,
        "speed_factor": speed,
        "seed": seed,
        "parallel_infer": True,
        "repetition_penalty": float(payload.get("repetition_penalty") or 1.35),
        "streaming_mode": False,
        "return_fragment": False,
    }

    # ⚠️ 整段串行：GPT-SoVITS 的 run() 是生成器，不是线程安全的；
    #    同一个引擎上并发跑会互相踩。NekoPal 那边有预合成队列，串行完全够用。
    with _LOCK:
        engine = ensure_engine(
            engine_dir,
            str(payload.get("device") or "cpu"),
            str(payload.get("version") or "v2ProPlus"),
            bool(payload.get("is_half", False)),
        )
        _switch_weights(engine, gpt, sovits)

        started = time.time()
        chunks = list(engine.run(request))
        elapsed = time.time() - started

    if not chunks:
        raise RuntimeError("推理没有返回任何音频")
    sample_rate = chunks[0][0]
    audio = np.concatenate([chunk[1] for chunk in chunks])
    if audio.size == 0 or not np.isfinite(audio).all():
        raise RuntimeError("推理返回的音频不合法")

    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
    _STATE["chunks"] += 1
    _STATE["seconds"] += elapsed
    return buffer.getvalue()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/health"):
            self._send_json(
                200,
                {
                    "status": "ok",
                    "engine": "GPT-SoVITS",
                    "engine_dir": str(DEFAULT_ENGINE),
                    "device": _DEVICE,
                    "version": _VERSION,
                    "loaded": _STATE["engine"] is not None,
                    "gpt_weights": _STATE["gpt"],
                    "sovits_weights": _STATE["sovits"],
                    "chunks": _STATE["chunks"],
                    "synth_seconds": round(_STATE["seconds"], 2),
                    "uptime": round(time.time() - _STARTED_AT, 1),
                },
            )
        else:
            self._send_json(404, {"status": "error", "message": "只有 /health 和 /tts"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/tts"):
            self._send_json(404, {"status": "error", "message": "只有 /health 和 /tts"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            audio = do_tts(payload)
            self._send_bytes(200, audio, "audio/wav")
        except Exception as exc:  # noqa: BLE001 - 一切失败都回 400 + 消息
            detail = "{}: {}".format(type(exc).__name__, exc)
            sys.stderr.write("[sovits] 失败：{}\n".format(detail))
            self._send_json(400, {"status": "error", "message": detail})

    def log_message(self, fmt: str, *args) -> None:  # 默认每请求都打，太吵
        if os.environ.get("NEKOPAL_SOVITS_DEBUG"):
            sys.stderr.write("[sovits] " + (fmt % args) + "\n")


_DEVICE = "cpu"
_VERSION = "v2ProPlus"


def main(argv=None) -> int:
    global _DEVICE, _VERSION

    parser = argparse.ArgumentParser(prog="serve.py", description="常驻 GPT-SoVITS 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9880)
    parser.add_argument("--engine-dir", default=str(DEFAULT_ENGINE),
                        help="GPT-SoVITS 本体在哪（默认 ~/.local/share/nekopal/sovits）")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"],
                        help="Apple Silicon 建议先用 cpu（mps 不一定稳）")
    parser.add_argument("--version", default="v2ProPlus",
                        help="要加载的模型版本，必须和权重的版本一致（交付包通常是 v2ProPlus）")
    parser.add_argument("--is-half", action="store_true", help="半精度（只有 GPU 才建议）")
    parser.add_argument("--no-warmup", action="store_true", help="启动时不预先加载引擎")
    args = parser.parse_args(argv)

    _DEVICE = args.device
    _VERSION = args.version

    if not args.no_warmup:
        print("[sovits] 预热引擎（第一次要几十秒，之后每句就快了）…", flush=True)
        try:
            ensure_engine(Path(args.engine_dir).expanduser(), args.device, args.version, args.is_half)
        except Exception as exc:  # noqa: BLE001 - 预热失败也让服务起来（请求时会再报一次）
            print("[sovits] ⚠️ 预热失败：{}: {}".format(type(exc).__name__, exc), flush=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        "GPT-SoVITS 服务已启动： http://{}:{}\n"
        "  NekoPal 默认就用这个地址（不用额外设置）\n"
        "  自检：curl http://{}:{}/health\n"
        "  停止：Ctrl+C".format(args.host, args.port, args.host, args.port),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
