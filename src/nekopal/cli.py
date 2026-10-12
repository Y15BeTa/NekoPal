"""命令行入口。

    nekopal serve          启动本地服务
    nekopal app            打开原生窗口（桌面外壳）
    nekopal open           在浏览器打开跟读窗
    nekopal read "文字"    命令行直接朗读（本机播放）
    nekopal export         导出音频文件（通勤播客）
    nekopal voices         列出可用音色
    nekopal packs          音色包 / 角色包管理（只做接口，不内置素材）
    nekopal convert        变声链路自检（RVC / DDSP-SVC）
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


def _cmd_export(args: argparse.Namespace) -> int:
    """把一整块文字合成并导出成一个音频文件（通勤播客）。"""

    from nekopal.core.cache import AudioCache
    from nekopal.core.export import ExportError, assemble, available_formats
    from nekopal.core.models import SynthOptions
    from nekopal.core.pipeline import prepare_sentences, synthesize_sentence
    from nekopal.tts.registry import build_registry

    settings = load_settings()

    text = args.text or ""
    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            text = handle.read()
    if not text.strip() and not sys.stdin.isatty():
        text = sys.stdin.read()
    if not text.strip():
        print("没有可导出的文字（用参数、--file 或 stdin 传入）", file=sys.stderr)
        return 2

    fmt = args.format or settings.export_format
    gap = settings.export_gap if args.gap is None else args.gap

    registry = build_registry()
    cache = AudioCache(settings.cache_dir, max_mb=settings.cache_max_mb)
    voice = registry.resolve_voice_id(
        args.voice or settings.default_voice, fallback=settings.fallback_voice
    )
    options = SynthOptions(rate=args.rate)
    sentences = prepare_sentences(text, {}, settings.clean)
    if not sentences:
        print("清洗后没有可朗读的内容", file=sys.stderr)
        return 2

    print("共 {} 句 · 音色 {} · 目标格式 {}".format(len(sentences), voice, fmt))

    async def collect():
        paths = []
        try:
            for index, sentence in enumerate(sentences, 1):
                segment, _ = await synthesize_sentence(
                    sentence, voice, options, registry, cache, settings.fallback_voice
                )
                paths.append(segment.path)
                print("  [{}/{}] {:5.2f}s  {}".format(index, len(sentences), segment.duration, sentence[:26]))
        finally:
            await registry.aclose()
        return paths

    paths = asyncio.run(collect())

    name = args.name or (Path(args.file).stem if args.file else "nekopal-export")
    out_dir = Path(args.out).expanduser() if args.out else settings.export_dir
    try:
        result = assemble(paths, out_dir / name, fmt=fmt, gap=gap)
    except ExportError as exc:
        print("导出失败：{}".format(exc), file=sys.stderr)
        usable = [key for key, ok in available_formats().items() if ok]
        print("本机可用格式：{}".format("、".join(usable) or "（无）"), file=sys.stderr)
        return 1

    print()
    print("✅ 已导出 {}".format(result["path"]))
    print(
        "   {} · {:.1f} 分钟 · {:.1f} MB · {} 段".format(
            result["format"],
            result["duration"] / 60.0,
            result["bytes"] / 1048576.0,
            result["segments"],
        )
    )
    return 0


def _cmd_packs(args: argparse.Namespace) -> int:
    """音色包 / 角色包管理 —— **只做接口，不内置任何素材**。"""

    from nekopal.packs import ensure_user_dirs, load_packs, search_dirs, write_template

    if args.action == "dir":
        voices_dir, characters_dir = ensure_user_dirs()
        print("把素材放到这里（它们在仓库外，天然不会被提交）：")
        print()
        print("  音色包  {}".format(voices_dir))
        print("  角色包  {}".format(characters_dir))
        print()
        print("模板： nekopal packs init voice <id>")
        if args.open:
            subprocess.run(["open", str(voices_dir)], check=False)
        return 0

    if args.action == "init":
        if not args.kind or not args.pack_id:
            print("用法： nekopal packs init <voice|character> <id>", file=sys.stderr)
            return 2
        try:
            path = write_template(args.kind, args.pack_id)
        except ValueError as exc:
            print("失败：{}".format(exc), file=sys.stderr)
            return 1
        print("已生成模板：{}".format(path))
        print("编辑它，再把素材放进同一目录，然后： nekopal packs list")
        return 0

    voice_dirs, character_dirs = search_dirs()
    print("扫描目录（从上到下优先级递减）：")
    for directory in voice_dirs:
        print("  音色包  {}".format(directory))
    for directory in character_dirs:
        print("  角色包  {}".format(directory))
    print()

    collection = load_packs()

    if collection.voices:
        print("音色包（{} 个）:".format(len(collection.voices)))
        for pack in collection.voices:
            flag = "  ⚠️ 未授权来源，仅供个人使用" if pack.license == "unofficial" else ""
            print("  {:<34} {:<8} {}{}".format(pack.full_id, pack.backend, pack.name, flag))
    else:
        print("音色包：无（nekopal packs init voice my-voice 可以建一个）")

    if collection.characters:
        print("角色包（{} 个）:".format(len(collection.characters)))
        for pack in collection.characters:
            state = "形象就绪" if pack.model_exists else "仅声音（模型缺失，朗读不受影响）"
            print("  {:<34} {:<30} {}".format(pack.id, state, pack.name))
    else:
        print("角色包：无")

    if collection.problems:
        print()
        print("已跳过（{} 个）:".format(len(collection.problems)))
        for problem in collection.problems:
            print("  ❌ {}".format(problem.path))
            print("     {}".format(problem.reason))
        print()
        print("跳过某个包不会影响其它包，也不会影响服务启动。")
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    """变声链路自检：合成一句 → 跑你的变声命令 → 报告结果。

    变声工具五花八门，本项目**不猜任何 API**，只约定「给一个 WAV，还一个 WAV」。
    这个命令就是让你确认那条命令到底能不能跑通。
    """

    from nekopal.core.convert import CommandConverter, ConvertError, ConvertRequest
    from nekopal.core.export import decode, is_target_wav, write_wav
    from nekopal.core.models import SynthOptions
    from nekopal.packs import load_packs
    from nekopal.tts.registry import build_registry

    settings = load_settings()
    registry = build_registry(settings)
    rvc = registry.get("rvc")
    if rvc is None:
        print("没有 rvc 后端（这不正常）", file=sys.stderr)
        return 1

    collection = load_packs(known_backends=[backend.id for backend in registry.all()])
    pack = None
    if args.pack:
        pack = next((item for item in collection.voices if item.id == args.pack), None)
        if pack is None:
            print("找不到音色包 `{}`。先跑 `nekopal packs list` 看看有哪些。".format(args.pack), file=sys.stderr)
            return 1

    # 变声命令：命令行 > 音色包 > 环境变量
    if args.convert_command:
        converter = CommandConverter(args.convert_command, timeout=settings.rvc_timeout)
    elif pack is not None and pack.command:
        converter = CommandConverter(pack.command, timeout=settings.rvc_timeout)
    else:
        converter = rvc.converter()

    if args.action == "show":
        print("变声命令 : {}".format(converter.describe() if converter else "（未配置）"))
        print("超时     : {:.0f} 秒".format(settings.rvc_timeout))
        print()
        print("配置方式（占位符 {input} {output} {model} {index} {pitch}）：")
        print("  export NEKOPAL_RVC_COMMAND='python infer.py {input} {output} --model {model}'")
        return 0

    if converter is None or not converter.available():
        print("还没配置变声命令 —— 这是唯一需要你手动做的事。", file=sys.stderr)
        print()
        print("命令模板里的占位符：")
        print("  {input}   合成出来的原始音频（WAV）")
        print("  {output}  变声结果要写到哪里（WAV）")
        print("  {model}   RVC / DDSP-SVC 的模型路径")
        print("  {index}   index 文件路径（可为空）")
        print("  {pitch}   变调半音数")
        print()
        print("例：")
        print("  export NEKOPAL_RVC_COMMAND='python infer.py {input} {output} --model {model} --index {index} --pitch {pitch}'")
        return 1

    model = args.model or (pack.rvc_model if pack else None)
    index = args.index or (pack.rvc_index if pack else None)
    base_voice = args.base_voice or (pack.base_voice if pack else None) or "say:auto"

    print("变声命令 : {}".format(converter.describe()))
    print("输入音色 : {}".format(base_voice))
    print("模型     : {}".format(model or "（未指定 —— 如果命令里用不到 {model} 就没关系）"))
    print()

    out_dir = Path(args.out).expanduser() if args.out else settings.cache_dir / "convert-test"
    out_dir.mkdir(parents=True, exist_ok=True)

    async def run() -> int:
        try:
            base = await registry.synth(base_voice, args.text, SynthOptions())
            print("① 合成完成 : {:5.2f}s · {} · {} 字节".format(
                base.duration, base.extension, len(base.audio)))

            stage = out_dir / "step1-base"
            stage.mkdir(parents=True, exist_ok=True)
            raw = stage / ("base." + base.extension)
            raw.write_bytes(base.audio)
            if is_target_wav(raw):
                source = raw
            else:
                source = stage / "base.wav"
                write_wav(decode(raw, stage), source)
            print("② 已转成变声输入 : {}".format(source))

            target = out_dir / "step2-converted.wav"
            await converter.convert(
                ConvertRequest(
                    input_path=source,
                    output_path=target,
                    model=model,
                    index=index,
                    pitch=0.0,
                )
            )
            from nekopal.media import wav_duration

            print("③ 变声完成 : {:5.2f}s → {}".format(wav_duration(target.read_bytes()), target))

            if args.play and sys.platform == "darwin":
                print("   播放中…")
                subprocess.run(["afplay", str(target)], check=False)

            print()
            print("✅ 链路通了。接下来只要把这个音色包配好，就能在跟读窗里选它。")
            return 0
        except ConvertError as exc:
            print()
            print("❌ 变声失败：{}".format(exc), file=sys.stderr)
            print()
            print("排查顺序：")
            print("  1. 命令第一段是可执行程序吗？（先在终端里手跑一遍同样的命令）")
            print("  2. {model} 指向的文件存在吗？")
            print("  3. 那个工具自己能跑吗？（缺依赖 / 缺模型 / 需要 GPU）")
            return 1
        except Exception as exc:  # noqa: BLE001
            print("❌ 失败：{}".format(exc), file=sys.stderr)
            return 1
        finally:
            await registry.aclose()

    return asyncio.run(run())


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

    export_cmd = sub.add_parser("export", help="导出音频（通勤播客）")
    export_cmd.add_argument("text", nargs="?", help="要导出的文字（省略则读 --file 或 stdin）")
    export_cmd.add_argument("--file", help="从文件读取（.md 会自动清洗）")
    export_cmd.add_argument("--format", default=None, choices=["wav", "m4a", "aac", "mp3"])
    export_cmd.add_argument("--voice", default=None)
    export_cmd.add_argument("--rate", type=float, default=1.0)
    export_cmd.add_argument("--out", default=None, help="输出目录（默认 ~/NekoPal/export）")
    export_cmd.add_argument("--name", default=None, help="文件名（不含扩展名）")
    export_cmd.add_argument("--gap", type=float, default=None, help="段间静音秒数（默认 0.15）")

    convert_cmd = sub.add_parser("convert", help="变声链路自检（RVC / DDSP-SVC）")
    convert_cmd.add_argument(
        "action", nargs="?", default="test", choices=["test", "show"]
    )
    convert_cmd.add_argument("--pack", default=None, help="用哪个音色包的定义")
    convert_cmd.add_argument("--base-voice", default=None, help="基础音色（先用谁合成）")
    convert_cmd.add_argument("--model", default=None, help="变声模型路径")
    convert_cmd.add_argument("--index", default=None, help="index 文件路径")
    # 注意：不能用 --command —— 它的 dest 会覆盖 add_subparsers(dest="command")
    convert_cmd.add_argument(
        "--convert-command", dest="convert_command", nargs="*", default=None,
        help="覆盖变声命令模板",
    )
    convert_cmd.add_argument(
        "--text", default="这是变声链路自检，如果听到这句话，说明配置正确。"
    )
    convert_cmd.add_argument("--out", default=None, help="中间产物放哪")
    convert_cmd.add_argument("--play", action="store_true", default=True)
    convert_cmd.add_argument("--no-play", dest="play", action="store_false")

    packs_cmd = sub.add_parser("packs", help="音色包 / 角色包（只做接口，不内置素材）")
    packs_cmd.add_argument(
        "action", nargs="?", default="list", choices=["list", "dir", "init"]
    )
    packs_cmd.add_argument("kind", nargs="?", choices=["voice", "character"])
    packs_cmd.add_argument("pack_id", nargs="?")
    packs_cmd.add_argument("--open", action="store_true", help="在 Finder 里打开目录")

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
        "export": _cmd_export,
        "packs": _cmd_packs,
        "convert": _cmd_convert,
        "autostart": _cmd_autostart,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
