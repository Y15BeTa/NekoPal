"""桌面外壳：把跟读窗放进一个原生窗口（设计文档 3.4）。

这里**只做一层壳**：后端是纯 API 服务，所以窗口只是「另一个客户端」。
`core/` `tts/` `server/` 与 `web/` 一行都不用改。

用法：

    nekopal app              # 打开原生窗口（后端没起就自动拉起）
    nekopal app --hidden     # 不开窗，只做启动自检（排查用）
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

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
