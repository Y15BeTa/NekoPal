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


def ensure_rvc_service(verbose: bool = True) -> bool:
    """需要时把常驻变声服务拉起来（已跑就跳过）。

    只有当你配了 ``NEKOPAL_RVC_ENDPOINT`` 且本机装了 RVC 的 venv 时才动手；
    否则静默跳过（不配变声的人不受影响）。
    """

    endpoint = (os.environ.get("NEKOPAL_RVC_ENDPOINT") or "").strip()
    if not endpoint:
        return False

    health = endpoint.rstrip("/") + "/health"
    if _url_alive(health):
        if verbose:
            print("变声服务已在运行")
        return True

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
        print("变声服务启动超时 —— 朗读仍可用，只是变声会退回本地推理（较慢）")
    return False


def stop_all(verbose: bool = True) -> int:
    """停掉 NekoPal 相关进程（窗口 / 后端 / 变声服务）。"""

    patterns = (
        ("nekopal app", "原生窗口"),
        ("nekopal serve", "NekoPal 服务"),
        ("integrations/rvc/serve.py", "变声服务"),
    )
    stopped = 0
    for pattern, label in patterns:
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
