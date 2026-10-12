"""开机自启（macOS LaunchAgent）。

    nekopal autostart status     查看状态
    nekopal autostart install    写入 LaunchAgent 并加载
    nekopal autostart uninstall  卸载并删除

只是把「服务常驻」这件事交给系统，不改变任何运行逻辑。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from nekopal.config import Settings, load_settings

LABEL = "ai.nekopal.serve"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "{}.plist".format(LABEL)


def _render(settings: Settings) -> str:
    workdir = Path.cwd()
    python = sys.executable
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>nekopal</string>
        <string>serve</string>
        <string>--host</string>
        <string>{host}</string>
        <string>--port</string>
        <string>{port}</string>
        <string>--log-level</string>
        <string>warning</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/nekopal.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/nekopal.err.log</string>
</dict>
</plist>
""".format(
        label=LABEL,
        python=python,
        host=settings.host,
        port=settings.port,
        workdir=workdir,
    )


def _uid() -> int:
    import os

    return os.getuid()


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, timeout=20
    )


def install(settings: Optional[Settings] = None) -> int:
    settings = settings or load_settings()
    if sys.platform != "darwin":
        print("开机自启目前只实现了 macOS（LaunchAgent）。", file=sys.stderr)
        return 1

    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(settings), encoding="utf-8")
    print("已写入 {}".format(path))

    _launchctl("bootout", "gui/{}/{}".format(_uid(), LABEL))  # 幂等：先卸再装
    result = _launchctl("bootstrap", "gui/{}".format(_uid()), str(path))
    if result.returncode != 0:
        print("加载失败：{}".format((result.stderr or result.stdout).strip()), file=sys.stderr)
        print("可手动重试： launchctl bootstrap gui/$(id -u) {}".format(path), file=sys.stderr)
        return 1

    print("已启用：登录后自动启动 NekoPal 服务（{}:{}）".format(settings.host, settings.port))
    return 0


def uninstall() -> int:
    path = plist_path()
    _launchctl("bootout", "gui/{}/{}".format(_uid(), LABEL))
    if path.exists():
        path.unlink()
        print("已删除 {}".format(path))
    else:
        print("（未安装）")
    print("已关闭开机自启。")
    return 0


def status() -> int:
    path = plist_path()
    print("plist : {} {}".format(path, "存在" if path.exists() else "不存在"))
    result = _launchctl("print", "gui/{}/{}".format(_uid(), LABEL))
    if result.returncode == 0:
        head = "\n".join(result.stdout.splitlines()[:6])
        print("状态  : 已加载\n{}".format(head))
    else:
        print("状态  : 未加载")
    return 0


def dispatch(action: str, settings: Optional[Settings] = None) -> int:
    if action == "install":
        return install(settings)
    if action == "uninstall":
        return uninstall()
    return status()
