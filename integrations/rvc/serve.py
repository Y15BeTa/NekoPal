#!/usr/bin/env python
"""常驻变声服务：模型只加载一次，之后每句只走一次 HTTP。

**为什么需要它**：`convert_cli.py` 每句都要新起一个进程 → 每次白付约 3.5 秒
（python + torch/faiss 导入 + HuBERT 加载）。实测单句端到端约 5.5 秒，
读长文时生产者跟不上播放。

本服务把模型常驻在内存里，实测稳态 RTF 0.39（4.27s 音频 → 1.67s）。

用法::

    # 启动（跑在装了 RVC 的那个 venv 里）
    ~/.local/share/nekopal/rvc-venv/bin/python integrations/rvc/serve.py --port 7899

    # 让 NekoPal 走它（convert_cli.py 会自动优先用服务）
    export NEKOPAL_RVC_ENDPOINT=http://127.0.0.1:7899

不启服务也能用 —— `convert_cli.py` 会自动退回本地推理（只是慢）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ⚠️ 必须在 import torch / faiss 之前（否则 macOS arm64 上 faiss 的 OpenMP 会段错误）
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

_LOCK = threading.Lock()
_STATE = {
    "loader": None,
    "config_key": None,
    "loaded_at": 0.0,
    "conversions": 0,
}


def _config_key(model: str, index: str, pitch_algo: str, only_cpu: bool) -> str:
    return "{}|{}|{}|{}".format(model, index, pitch_algo, only_cpu)


def ensure_loader(model: str, index: str, pitch_algo: str, only_cpu: bool):
    """确保内存里的模型就是要用的那个；换了模型就重新加载。"""

    key = _config_key(model, index, pitch_algo, only_cpu)

    if _STATE["loader"] is not None and _STATE["config_key"] == key:
        return _STATE["loader"]

    from infer_rvc_python import BaseLoader

    if _STATE["loader"] is None:
        _STATE["loader"] = BaseLoader(only_cpu=only_cpu)
    elif _STATE["config_key"] != key:
        try:
            _STATE["loader"].unload_models()
        except Exception:  # noqa: BLE001 - 卸载失败不影响重载
            pass

    _STATE["loader"].apply_conf(
        tag="nekopal",
        file_model=model,
        file_index=index or "",
        pitch_algo=pitch_algo,
        pitch_lvl=0,  # pitch 在每次 convert 时单独处理（见下）
    )
    _STATE["config_key"] = key
    _STATE["loaded_at"] = time.time()
    return _STATE["loader"]


def do_convert(payload: dict) -> dict:
    import soundfile as sf

    model = str(payload.get("model") or "")
    index = str(payload.get("index") or "")
    pitch = int(round(float(payload.get("pitch") or 0)))
    pitch_algo = str(payload.get("pitch_algo") or "pm")
    only_cpu = bool(payload.get("only_cpu", True))
    # 气声相关三件套（设计文档 6.9 / 音色调节）：不传就用 RVC 的默认值。
    # 这三个正是「声音有点虚 / 想要气音」主要能调的地方。
    breath = float(payload.get("breath_protection") or 0.33)
    envelope = float(payload.get("envelope_ratio") or 0.25)
    respiration = int(payload.get("respiration_filter") or 3)
    source = Path(str(payload.get("input") or ""))
    target = Path(str(payload.get("output") or ""))

    if not source.is_file():
        raise ValueError("输入音频不存在：{}".format(source))
    if not model or not Path(model).is_file():
        raise ValueError("模型不存在：{}".format(model))

    data, sample_rate = sf.read(str(source), dtype="int16")

    with _LOCK:
        loader = ensure_loader(model, index, pitch_algo, only_cpu)
        # pitch 变化不需要重载模型，重新 apply_conf 一次即可（很便宜）
        loader.apply_conf(
            tag="nekopal",
            file_model=model,
            file_index=index,
            pitch_algo=pitch_algo,
            pitch_lvl=pitch,
            consonant_breath_protection=breath,
            envelope_ratio=envelope,
            respiration_median_filtering=respiration,
        )
        started = time.time()
        result, out_rate = loader.generate_from_cache(
            audio_data=(data, sample_rate), tag="nekopal"
        )
        elapsed = time.time() - started

    if result is None:
        raise ValueError("推理没有返回结果")

    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), result, out_rate)
    _STATE["conversions"] += 1

    return {
        "ok": True,
        "seconds": round(elapsed, 3),
        "audio_seconds": round(len(data) / sample_rate, 3),
        "rtf": round(elapsed / (len(data) / sample_rate), 3) if data.size else 0.0,
        "sample_rate": out_rate,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/health"):
            self._send(
                200,
                {
                    "ok": True,
                    "loaded": _STATE["config_key"] is not None,
                    "config": _STATE["config_key"],
                    "conversions": _STATE["conversions"],
                    "uptime": round(time.time() - _STARTED_AT, 1),
                },
            )
        else:
            self._send(404, {"ok": False, "message": "只有 /health 和 /convert"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/convert"):
            self._send(404, {"ok": False, "message": "只有 /health 和 /convert"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = do_convert(payload)
            self._send(200, result)
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"ok": False, "message": "{}: {}".format(type(exc).__name__, exc)})

    def log_message(self, fmt: str, *args) -> None:  # 默认每个请求都打日志，太吵
        if os.environ.get("NEKOPAL_RVC_DEBUG"):
            sys.stderr.write("[rvc] " + (fmt % args) + "\n")


_STARTED_AT = time.time()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="serve.py", description="常驻 RVC 变声服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7899)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        "RVC 变声服务已启动： http://{}:{}\n"
        "  让 NekoPal 用它： export NEKOPAL_RVC_ENDPOINT=http://{}:{}\n"
        "  停止： Ctrl+C".format(args.host, args.port, args.host, args.port),
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
