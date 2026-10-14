"""`ui.json` 持久化的**完整性**检查。

## 为什么专门为它写一个测试

`_save_ui()` 有个很坑的设计：它**只保留 `_load_ui()` 里认识的键**，
其它一律**静默丢掉** —— 不报错、不警告，界面上看起来"保存成功"。

于是同一个坑被踩了**四次**：
  ① `voice`（选了要楽奈，重启变回晓晓）
  ② `auto_read`（关了自动朗读，重启又开了）
  ③ `pitch`（调了音高，重启回到 0）
  ④ `idle_*`（挂机间隔调成 1 分钟，重启回到 8 分钟）

所以这里钉一条：**凡是要存进 ui.json 的键，必须先在 defaults 里登记**。
"""

from __future__ import annotations

import re

from nekopal.server import routes

#: `PUT /api/settings` 里那句 `_save_ui(...)` 的键清单（从源码里读，避免手抄漏）
SOURCE = (routes.__file__,)


def _persisted_keys() -> set:
    """从源码里把 `_save_ui` 那句 `for k in (...)` 的键抠出来。"""

    src = open(SOURCE[0], encoding="utf-8").read()
    blocks = re.findall(r"for k in \(\s*((?:\s*\"[a-z_]+\",)+)\s*\)", src)
    keys = set()
    for b in blocks:
        keys.update(re.findall(r"\"([a-z_]+)\"", b))
    return keys


def test_every_persisted_key_is_known(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    keys = _persisted_keys()
    assert keys, "没从源码里找到 _save_ui 的键清单（正则失配了？）"

    # 全部写一遍，再读回来 —— 少一个就说明它被静默丢了
    payload = {k: _sample(k) for k in keys}
    routes._save_ui(payload)
    got = routes._load_ui()

    lost = sorted(k for k in keys if k not in got)
    assert not lost, (
        "这些键写了 ui.json 但读不回来（`_load_ui` 的 defaults 里没有它们）：{}".format(lost)
    )


def test_values_survive_a_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    routes._save_ui(
        {
            "idle_enabled": False,
            "idle_min_minutes": 3,
            "idle_hours": "7-22",
            "auto_read": False,
            "pitch": 2.0,
        }
    )
    got = routes._load_ui()
    assert got["idle_enabled"] is False
    assert got["idle_min_minutes"] == 3
    assert got["idle_hours"] == "7-22"
    assert got["auto_read"] is False
    assert got["pitch"] == 2.0


def _sample(key: str):
    """给每个键一个"一眼能认出来"的样例值（类型不重要，能过 round-trip 就行）。"""
    return {
        "idle_hours": "7-22",
        "auto_scroll": "off",
        "line_source": "live",
        "character": "rana",
        "outfit": "some-outfit",
        "voice": "rvc:rana",
    }.get(key, 3)
