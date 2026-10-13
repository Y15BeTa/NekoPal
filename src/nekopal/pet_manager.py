"""角色窗口（桌宠）进程的生命周期 —— **由后端负责**。

## 为什么是后端

"要不要开人物形象"是主窗上的一个开关 ✗，而**只有后端**同时满足两个条件：

1. 它知道开关的状态（`ui.json` 存在它这边 ✗）
2. 它能起进程（网页不能 ✗）

而且主窗关掉时角色窗口也该一起关 ✗ —— 后端正好是那个"活得比窗口久一点"的东西。

> 早先是在 `run_app()` 里顺带拉起的 ✗ —— 那样有两个问题：
> `nekopal serve` 单独跑时不会有人物 ✗；而且和"主窗里切换开关"是两套逻辑 ✗。
> 现在**只有这一个 owner** ✗。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

_proc: Optional[subprocess.Popen] = None


def _ui_file() -> Path:
    from nekopal.packs import config_dir

    return config_dir() / "ui.json"


def show_avatar_wanted() -> bool:
    """读 ui.json 看主窗要不要开人物形象（没有 / 坏了 → 默认开 ✗）。"""

    try:
        data = json.loads(_ui_file().read_text(encoding="utf-8"))
        return bool(data.get("show_avatar", True))
    except Exception:  # noqa: BLE001
        return True


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def ensure(want: bool) -> bool:
    """按需拉起 / 收掉角色窗口。返回它现在是否在跑。"""

    global _proc

    if want and not running():
        from nekopal.config import load_settings
        from nekopal.desktop import spawn_pet

        _proc = spawn_pet(load_settings())
        if _proc is not None:
            print("角色窗口已拉起（由后端负责，关后端会一起关）", flush=True)
    elif not want and running():
        assert _proc is not None
        _proc.terminate()
        _proc = None
        print("角色窗口已收掉（主窗的开关关掉了）", flush=True)

    return running()


def stop() -> None:
    """后端退出时收掉它（别留下孤儿窗口 ✗）。"""

    global _proc

    if running():
        assert _proc is not None
        _proc.terminate()
    _proc = None
