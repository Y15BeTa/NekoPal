"""命令行入口。

    nekopal serve          启动本地服务
    nekopal app            打开原生窗口（桌面外壳）
    nekopal open           在浏览器打开跟读窗
    nekopal read "文字"    命令行直接朗读（本机播放）
    nekopal voices         列出可用音色
    nekopal autostart ...  开机自启（macOS LaunchAgent）
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import List, Optional

from nekopal import __version__
from nekopal.config import load_settings


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from nekopal.server.app import create_app

    settings = load_settings()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    url = "http://{}:{}/".format(settings.host, settings.port)
    print("NekoPal {}  跟读窗: {}".format(__version__, url))
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=args.log_level)
    return 0


def _cmd_open(args: argparse.Namespace) -> int:
    settings = load_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    webbrowser.open("http://{}:{}/".format(host, port))
    return 0


def _cmd_voices(args: argparse.Namespace) -> int:
    settings = load_settings()

    async def collect():
        from nekopal.tts.registry import build_registry

        registry = build_registry()
        try:
            return await registry.voices()
        finally:
            await registry.aclose()

    voices = asyncio.run(collect())
    for voice in voices:
        marker = "  <- 默认" if voice.id == settings.default_voice else ""
        print("{:<44} {:<6} {}{}".format(voice.id, voice.backend, voice.name, marker))
    print("共 {} 个音色".format(len(voices)))
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    settings = load_settings()

    async def run():
        from nekopal.core.models import SynthOptions
        from nekopal.core.textproc import apply_clean
        from nekopal.tts.registry import build_registry

        registry = build_registry()
        try:
            voice = registry.resolve_voice_id(args.voice or settings.default_voice)
            text = apply_clean(args.text, enabled=settings.clean)
            return await registry.synth(voice, text, SynthOptions(rate=args.rate))
        finally:
            await registry.aclose()

    result = asyncio.run(run())
    out = Path(tempfile.gettempdir()) / "nekopal-read.{}".format(result.extension)
    out.write_bytes(result.audio)
    print("已生成 {}（{:.2f}s，{} 字节）".format(out, result.duration, len(result.audio)))

    if args.play:
        player = "afplay" if sys.platform == "darwin" else None
        if player is None:
            print("（本机没有 afplay，跳过播放）")
        else:
            try:
                subprocess.run([player, str(out)], check=False)
            except OSError as exc:
                print("播放失败：{}".format(exc))
    return 0


def _cmd_app(args: argparse.Namespace) -> int:
    from nekopal.desktop import run_app

    settings = load_settings()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    return run_app(settings, hidden=args.hidden, width=args.width, height=args.height)


def _cmd_autostart(args: argparse.Namespace) -> int:
    from nekopal.autostart import dispatch

    settings = load_settings()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    return dispatch(args.action, settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nekopal", description="把一整块文字交给一个角色，读给你听"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动本地服务")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--log-level", default="info")

    open_cmd = sub.add_parser("open", help="在浏览器打开跟读窗")
    open_cmd.add_argument("--host", default=None)
    open_cmd.add_argument("--port", type=int, default=None)

    read = sub.add_parser("read", help="命令行直接朗读一段文字")
    read.add_argument("text")
    read.add_argument("--voice", default=None)
    read.add_argument("--rate", type=float, default=1.0)
    read.add_argument("--play", action="store_true", default=True)
    read.add_argument("--no-play", dest="play", action="store_false")

    sub.add_parser("voices", help="列出可用音色")

    app_cmd = sub.add_parser("app", help="打开原生窗口（桌面外壳）")
    app_cmd.add_argument("--host", default=None)
    app_cmd.add_argument("--port", type=int, default=None)
    app_cmd.add_argument("--hidden", action="store_true", help="不开窗，只做启动自检")
    app_cmd.add_argument("--width", type=int, default=1040)
    app_cmd.add_argument("--height", type=int, default=780)

    auto = sub.add_parser("autostart", help="开机自启（macOS LaunchAgent）")
    auto.add_argument(
        "action", nargs="?", default="status", choices=["status", "install", "uninstall"]
    )
    auto.add_argument("--host", default=None)
    auto.add_argument("--port", type=int, default=None)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "serve": _cmd_serve,
        "app": _cmd_app,
        "open": _cmd_open,
        "read": _cmd_read,
        "voices": _cmd_voices,
        "autostart": _cmd_autostart,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
