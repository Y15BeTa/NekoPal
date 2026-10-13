"""桌面外壳：把跟读窗放进一个原生窗口（设计文档 3.4）。

这里**只做一层壳**：后端是纯 API 服务，所以窗口只是「另一个客户端」。
`core/` `tts/` `server/` 与 `web/` 一行都不用改。

用法：

    nekopal app              # 打开原生窗口（后端没起就自动拉起）
    nekopal app --hidden     # 不开窗，只做启动自检（排查用）
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from nekopal import __version__, pet_panel
from nekopal.config import Settings, load_settings

WINDOW_TITLE = "NekoPal 猫伴"

#: 置顶小条的标题与默认尺寸（够放一只猫 + 两个按钮）
PET_TITLE = "猫伴"
PET_SIZE = (236, 86)  # 小条形态（nekopal pet --strip）
# 角色形态（默认）：一个人物 + 悬停浮现的按钮列
#
# ⚠️ 宽度要和**角色本人**的宽高比对齐，不能按画布 ✗
# 实测：这个形象的「真实包围盒」是 0.707×1.179 → **宽高比 0.600**
#      （画布是 1×1.5 = 0.667，比角色宽，两侧有留白 ✗）
# 窗口比她宽的话，两侧就是空的 → 看起来"又小又细" ✗（用户实测反馈过）
# 这里 360 宽 - 左边 86（按钮列）/ 右边 10 = 264；460 高 - 上下各 10 = 440
# → 264/440 = **0.600** ✅ 和她一模一样
CHARACTER_SIZE = (360, 460)


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


def _run_pet_window(
    page: str,
    size: tuple,
    hidden: bool,
    settings: Settings,
    click_through: bool,
) -> int:
    """把窗口开出来：**能用 macOS 原生外壳就用原生**，否则退回 pywebview。

    为什么优先原生：只有 ``NSPanel`` 能**浮在别的 App 的全屏之上**，
    pywebview 的窗口做不到（根因与证据见 ``nekopal.pet_panel`` 的模块注释）。
    """

    url = "http://{}:{}{}".format(settings.host, settings.port, page)
    width, height = size

    # ⚠️ hidden（--hidden，给测试用）时走 pywebview：原生外壳不方便"隐藏启动"。
    if not hidden and pet_panel.available():
        try:
            return pet_panel.run(
                url,
                width=width,
                height=height,
                click_through=click_through,
                # 记住窗口位置：你拖到顺手的地方后，重启还在那儿
                state_path=str(settings.cache_dir / "pet-window.json"),
            )
        except Exception as exc:  # noqa: BLE001 - 兜底：退回 pywebview，别让你没得用
            print(
                "原生外壳启动失败（{}），改用 pywebview。".format(exc),
                file=sys.stderr,
            )

    try:
        import webview
    except ImportError:
        print(
            "缺少 pywebview。安装： uv pip install -e \".[desktop]\"",
            file=sys.stderr,
        )
        return 1

    webview.create_window(
        PET_TITLE,
        url,
        width=width,
        height=height,
        frameless=True,     # 无边框
        transparent=True,   # 透明背景（桌宠的根基）
        on_top=True,        # 本空间内始终在最前（跨全屏要 NSPanel，见上）
        easy_drag=True,     # 拖哪儿都能挪
        resizable=False,
        hidden=hidden,
    )
    webview.start(
        private_mode=False,
        storage_path=str(settings.cache_dir / "webview"),
    )
    return 0


def run_pet(
    settings: Optional[Settings] = None, hidden: bool = False, strip: bool = False
) -> int:
    """打开角色窗口：透明 / 无边框 / 全局置顶 / 可拖动。

    **默认是「角色形态」**：平时只有一个人物，鼠标移到它身上才浮出旁边的
    半透明按钮列（`web/character.html`）。这是桌宠的标准形态 ——
    平时不占眼睛，要用的时候按钮自己就来了。

    ``strip=True`` 回到旧的「小条形态」（`web/pet.html`）：一条窄栏，
    按钮常驻。适合只想快速点一下暂停/继续的场景。

    两者都跑在 macOS 原生 ``NSPanel`` 上（见 ``_run_pet_window``），
    所以**都能浮在别的 App 全屏之上** —— 你全屏看视频时它们也不会消失。

    为什么它比全局快捷键更好：**它一直在那儿** —— 点一下就暂停/继续，
    不用切窗口，也不用记组合键。而快捷键那套仍然保留作为备选。
    """

    settings = settings or load_settings()
    base = "http://{}:{}".format(settings.host, settings.port)

    # ⚠️ **先收掉旧后端** ✗ —— App 必须是唯一 owner ✗
    #    不然会出现「关 App 收不掉人物 / 下次复用旧代码」的鬼故事 ✗（真踩过）
    if not hidden:
        reclaim_backend(settings)

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

    if strip:
        # 小条是实心的，不需要精确命中（开着反而会让它点不着）
        page, size, click_through = "/pet.html", PET_SIZE, False
    else:
        # 角色是"非矩形"的，必须开精确命中：否则大透明窗口会挡住你点桌面
        page, size, click_through = "/character.html", CHARACTER_SIZE, True

    try:
        return _run_pet_window(page, size, hidden, settings, click_through)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()


def spawn_pet(settings: Settings) -> Optional[subprocess.Popen]:
    """把「角色窗口」作为子进程拉起来。

    ⚠️ **谁调用它**：`nekopal.pet_manager`（后端侧 ✗），
    不是主窗 —— 因为"要不要开人物"是主窗上的一个开关 ✗，
    而只有后端同时知道开关状态、又能起进程 ✗。

    ⚠️ 输出**必须落盘** ✗：早先接的是 DEVNULL ✗，结果后端拉起的角色窗
    出问题时**一点线索都没有**（页面里的报错是靠日志传出来的 ✗，日志没了就全瞎 ✗）。
    """

    # 日志目录（和 launch.log / serve.log 放一起 ✗）
    log_dir = Path.home() / "Library" / "Logs" / "NekoPal"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        sink = open(log_dir / "pet.log", "a", buffering=1, encoding="utf-8")
    except Exception:  # noqa: BLE001 - 日志目录建不出来也不能不起窗
        sink = subprocess.DEVNULL  # type: ignore[assignment]

    cmd = [
        sys.executable,
        "-m",
        "nekopal",
        "pet",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
    ]
    try:
        return subprocess.Popen(cmd, stdout=sink, stderr=subprocess.STDOUT)
    except Exception:  # noqa: BLE001
        return None


def reclaim_backend(settings: Settings) -> None:
    """把端口上那个**旧 nekopal 后端**收掉，好让本次 App 起一个全新的。

    ## 为什么必须这么做（真踩过 ✗）

    早先 `run_app` 只是"后端没在跑才起" ✗ —— 于是：

      · 有人（脚本 / 上次没收干净 ✗）已经在跑一个后端
        → App **不自己起** → 也**不拥有**它 ✗
      · 关 App 时**不会收掉**它 ✗ → 下次开 App 又**复用它** ✗
      · 结果：**永远在跑旧代码** ✗，而且**角色窗口归那个旧后端管** ✗
        → 关 App 时人物也不关 ✗（用户报的正是这个 ✗）

    ⇒ 结论：**App 必须是唯一 owner** ✗。开 App = 全新一套 ✗，关 App = 全清 ✗。
    """

    port = settings.port
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-t", "-iTCP:{}".format(port), "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001 - 没有 lsof 就算了
        return

    for raw in out.split():
        try:
            pid = int(raw)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            cmd = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception:  # noqa: BLE001
            continue
        # ⚠️ 只收**自家的**：端口上可能是别的东西 ✗
        if "nekopal" not in cmd:
            continue
        print(
            "发现旧的 NekoPal 后端（PID {}）—— 收掉它，本次起一个全新的".format(pid),
            flush=True,
        )
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            continue

    # 等它真的退（最多 5 秒 ✗）
    for _ in range(50):
        if not backend_alive("http://{}:{}".format(settings.host, port)):
            return
        time.sleep(0.1)


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
