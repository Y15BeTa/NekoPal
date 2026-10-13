"""桌面外壳：把跟读窗放进一个原生窗口（设计文档 3.4）。

这里**只做一层壳**：后端是纯 API 服务，所以窗口只是「另一个客户端」。
`core/` `tts/` `server/` 与 `web/` 一行都不用改。

用法：

    nekopal app              # 打开原生窗口（后端没起就自动拉起）
    nekopal app --hidden     # 不开窗，只做启动自检（排查用）
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from nekopal import __version__
from nekopal.config import Settings, load_settings

WINDOW_TITLE = "NekoPal 猫伴"

#: 置顶小条的标题与默认尺寸（够放一只猫 + 两个按钮）
PET_TITLE = "猫伴"
PET_SIZE = (236, 86)


def backend_alive(base: str, timeout: float = 0.6) -> bool:
    """探测后端是否已在运行。

    注意拼接：``base`` 不带尾斜杠，这里补一个 —— 曾经写成 ``base + "/api/health"``
    而 base 自带尾斜杠，产生 ``//api/health`` 直接 404，导致"以为后端没起"。
    """

    url = base.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _url_alive(url: str, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def default_rvc_python() -> Path:
    override = os.environ.get("NEKOPAL_RVC_PYTHON")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "nekopal" / "rvc-venv" / "bin" / "python"


#: 变声服务的进程特征（`stop_all` / `stop_rvc_service` 用它匹配）
RVC_SERVE_PATTERN = "integrations/rvc/serve.py"


def ensure_rvc_service(verbose: bool = True) -> bool:
    """需要时把常驻变声服务拉起来。

    返回值是 **「是不是我们拉起来的」** —— 是的话，调用方收尾时要负责停掉它，
    这样"关掉窗口 = 全部关闭"才成立（见 ``cli._cmd_up``）。

    只有配了 ``NEKOPAL_RVC_ENDPOINT`` 且本机装了 RVC 的 venv 时才动手；
    否则静默跳过（不配变声的人完全不受影响）。
    """

    endpoint = (os.environ.get("NEKOPAL_RVC_ENDPOINT") or "").strip()
    if not endpoint:
        return False

    health = endpoint.rstrip("/") + "/health"
    if _url_alive(health):
        if verbose:
            print("变声服务已在运行（不是本次拉起的，退出时也不会动它）")
        return False  # 别人已经在跑，不归我们管

    rvc_python = default_rvc_python()
    script = repo_root() / "integrations" / "rvc" / "serve.py"
    if not rvc_python.is_file() or not script.is_file():
        if verbose:
            print("（没找到变声环境，跳过；朗读会在没有变声的情况下工作）")
        return False

    try:
        port = urlparse(endpoint).port or 7899
    except ValueError:
        port = 7899

    if verbose:
        print("启动变声服务…")
    subprocess.Popen(
        [str(rvc_python), str(script), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(repo_root()),
    )
    for _ in range(120):  # 最多等 30 秒（首次加载模型较慢）
        if _url_alive(health):
            if verbose:
                print("变声服务就绪")
            return True
        time.sleep(0.25)

    if verbose:
        print("变声服务启动超时 —— 朗读仍可用，变声会退回本地推理（较慢）")
    return True  # 进程是我们起的，收尾时照样要停


def stop_rvc_service(verbose: bool = True) -> bool:
    """**只**停变声服务（不碰窗口与后端）。返回是否真的停掉了。"""

    result = subprocess.run(["pkill", "-f", RVC_SERVE_PATTERN], capture_output=True)
    if result.returncode == 0:
        if verbose:
            print("已停止变声服务")
        return True
    return False


#: `stop_all` 要停掉的进程（模式, 说明）。抽成模块常量是为了能写测试 —— 这里漏一项就会
#: "点了关闭却关不干净"。
#: ⚠️ 必须同时含 `up`：`nekopal up` 是在**同一进程内**调用 run_app 开窗的，
#: 它的命令行里根本没有 "nekopal app" 这个子串（踩过）。
STOP_PATTERNS = (
    ("nekopal app", "原生窗口"),
    ("nekopal up", "原生窗口（一键启动）"),
    ("nekopal serve", "NekoPal 服务"),
    (RVC_SERVE_PATTERN, "变声服务"),
)


def stop_all(verbose: bool = True) -> int:
    """停掉 NekoPal 相关进程（窗口 / 后端 / 变声服务）。"""

    stopped = 0
    for pattern, label in STOP_PATTERNS:
        result = subprocess.run(["pkill", "-f", pattern], capture_output=True)
        if result.returncode == 0:
            stopped += 1
            if verbose:
                print("已停止 {}".format(label))
    if stopped == 0 and verbose:
        print("没有正在运行的 NekoPal 进程")
    return stopped


def repo_root() -> Path:
    # src/nekopal/desktop.py -> src/nekopal -> src -> <repo>
    return Path(__file__).resolve().parents[2]


def spawn_backend(settings: Settings) -> subprocess.Popen:
    """把后端作为子进程拉起。

    不复用当前进程内的 uvicorn：独立进程更简单，也允许窗口关掉后服务继续活着
    （插件推送仍能工作）。
    """

    cmd = [
        sys.executable,
        "-m",
        "nekopal",
        "serve",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--log-level",
        "warning",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _pin_all_spaces(native) -> None:
    """给原生窗口设 collectionBehavior：**加入所有空间 + 全屏辅助**。"""

    try:
        from AppKit import (  # type: ignore[import-not-found]
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )
    except Exception as exc:  # noqa: BLE001 - 没装 pyobjc 就跳过
        print("置顶小条：没有 pyobjc（{}），跳过「所有空间可见」".format(exc), flush=True)
        return

    try:
        native.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        print("置顶小条：已设为「所有空间可见」（切到全屏 App 也不会消失）", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("置顶小条：设置「所有空间可见」失败：{}".format(exc), flush=True)


def _pin_all_spaces_when_ready(window) -> None:  # pragma: no cover - 已停用，见下
    """**已停用**：留在这里是为了记住这个坑。

    实测：**这个 pywebview 版本里读 `window.native` 就会崩**
    （`SIGTRAP` / `regionIneligible`，栈上全是 `NSWindow` / `objc`；
    后台线程和 `loaded` 事件两条路都试过，都崩）。

    所以「切到全屏 App 也不消失」这一条**目前做不到** —— 已回退，小条恢复可用。
    需要它的时候，回到 `nekopal control <action>` + 全局快捷键那条路
    （快捷键**不受空间限制**，全屏时照样能用）。
    """


def run_pet(settings: Optional[Settings] = None, hidden: bool = False) -> int:
    """打开「置顶小条」：透明 / 无边框 / 全局置顶 / 可拖动。

    这是**桌宠（角色层）的第一步** —— 先把「置顶窗口 + 点击控制」这套基础设施做出来，
    等有 Live2D 模型后，同一个窗口就换成角色本体（设计文档 6.5 / 6.9）。

    为什么它比全局快捷键更好：**它一直在那儿** —— 点一下就暂停/继续，
    不用切窗口，也不用记组合键。而快捷键那套仍然保留作为备选。
    """

    try:
        import webview
    except ImportError:
        print(
            "缺少 pywebview。安装： uv pip install -e \".[desktop]\"",
            file=sys.stderr,
        )
        return 1

    settings = settings or load_settings()
    base = "http://{}:{}".format(settings.host, settings.port)

    child: Optional[subprocess.Popen] = None
    if not backend_alive(base):
        print("后端未运行，正在拉起…")
        child = spawn_backend(settings)
        for _ in range(60):
            if backend_alive(base):
                break
            time.sleep(0.25)
        else:
            print("后端启动失败：请手动运行 `nekopal serve` 后再试。", file=sys.stderr)

    width, height = PET_SIZE
    webview.create_window(
        PET_TITLE,
        base + "/pet.html",
        width=width,
        height=height,
        frameless=True,     # 无边框
        transparent=True,   # 透明背景（桌宠的根基）
        on_top=True,        # 本空间内始终在最前
        easy_drag=True,     # 拖哪儿都能挪
        resizable=False,
        hidden=hidden,
    )

    try:
        # ⚠️ 这里**故意不做**「加入所有空间」的尝试：
        #    实测这个 pywebview 版本里读 `window.native` 会直接 SIGTRAP 崩掉
        #    （后台线程 / loaded 事件两条路都试过）。小条先保持可用；
        #    全屏场景请用 `nekopal control` + 全局快捷键（快捷键不受空间限制）。
        webview.start(
            private_mode=False,
            storage_path=str(settings.cache_dir / "webview"),
        )
    finally:
        if child is not None and child.poll() is None:
            child.terminate()

    return 0


def run_app(
    settings: Optional[Settings] = None,
    hidden: bool = False,
    width: int = 1040,
    height: int = 780,
) -> int:
    try:
        import webview
    except ImportError:
        print(
            "缺少 pywebview。安装： uv pip install -e \".[desktop]\"",
            file=sys.stderr,
        )
        return 1

    settings = settings or load_settings()
    base = "http://{}:{}".format(settings.host, settings.port)
    # ?shell=app 让页面知道自己跑在原生外壳里（提示文案与探针行为会不同）
    url = base + "/?shell=app"

    child: Optional[subprocess.Popen] = None
    if not backend_alive(base):
        print("后端未运行，正在拉起…")
        child = spawn_backend(settings)
        for _ in range(60):
            if backend_alive(base):
                break
            time.sleep(0.25)
        else:
            print("后端启动失败：请手动运行 `nekopal serve` 后再试。", file=sys.stderr)

    if hidden:
        # 自检模式：仍然创建 WebView（页面会执行），但不显示窗口
        print("NekoPal {} · 外壳自检（窗口隐藏）· {}".format(__version__, url))

    webview.create_window(
        WINDOW_TITLE,
        url,
        width=width,
        height=height,
        min_size=(720, 520),
        hidden=hidden,
        text_select=True,
        zoomable=True,
    )

    try:
        webview.start(
            private_mode=False,  # 保留本地存储（记住音色/语速等选择）
            storage_path=str(settings.cache_dir / "webview"),
        )
    finally:
        if child is not None and child.poll() is None:
            child.terminate()

    return 0
