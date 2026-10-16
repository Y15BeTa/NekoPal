"""首次运行：把**用户工作区**准备好。

## 为什么需要它

2026-10-14 实测：在一台"全新电脑"上（空的 `XDG_CONFIG_HOME`）跑一遍后端，
`~/.config/nekopal/` **连文件夹都不会被创建** ✗，所有接口都正常返回，
但用户**没有任何地方可以放自己的东西**，也没有任何提示 ✗。

于是「把项目 pull 到另一台电脑」会发生：

- 程序**能跑** ✅（退回系统朗读 + 静态台词，不会崩）
- 但：音色模型、Live2D 模型、`chat.yaml`、`profile.md`、`lines.yaml`
  **全都没有**，而且**不知道该放哪儿** ✗

这个模块补上前半截：**建目录 + 生成模板文件** ✅
（模型素材仍然要你自己放 —— 那是有版权的东西，程序不代为分发 ✗）。

## 红线

- **只创建、绝不覆盖** ✗：已有的文件一个字节都不动 ✅
- **不放进仓库**：这些都是写到 `~/.config/nekopal/`（仓库外）✗
- **不写任何密钥**：`chat.yaml` 模板里 `api_key` 是空字符串 ✗
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "ensure_user_workspace",
    "CHAT_TEMPLATE",
    "PROFILE_TEMPLATE",
    "README_TEXT",
]


CHAT_TEMPLATE = """# NekoPal 的「角色聊天」配置（让角色按性格自己说话）
#
# ⚠️ 这个文件在**仓库外**，里面会有 api_key —— 别提交、别外发。
#
# 留空 = 不启用 AI → 自动退回角色包里的静态台词（完全能用）

# OpenAI 兼容的根地址（**不要带** /chat/completions）
# 实测可用的一份（DeepSeek 官方，模型 ID 来自它的 /models 接口）：
#   base_url: "https://api.deepseek.com"
#   model:    "deepseek-flash"
base_url: ""

# 你的 key（只有这个文件里有它；接口只回报"有没有"，不回内容）
api_key: ""

# 模型名（用 OpenAI 兼容网关时，填它在 /models 里列出的 ID）
model: ""

temperature: 1.0
max_tokens: 400
timeout: 45
"""

PROFILE_TEMPLATE = """# 关于我（私人信息 —— 只在本机）

> 这份文件的内容会被**塞进提示词发给大模型** —— 所以：
> **别放不该外发的东西**（手机号 / 邮箱 / 证件 / 住址）。
> 不想要哪条，直接删掉那一行即可。
>
> 写得越具体，角色说的话越像"在跟你说话"而不是背台词。

## 怎么称呼我

- 叫我 ____

## 我现在的身份

- ____

## 我最近在忙什么

- ____

## 我在意的事 / 我的状态

- ____

## 我喜欢的东西

- ____

## 我希望你怎么对我

- ____
"""

README_TEXT = """NekoPal 的用户数据目录
======================

这个目录是**程序的家**（XDG 惯例），不在项目仓库里。

自动生成的文件（程序首次运行会建好模板）
----------------------------------------
  chat.yaml     大模型配置（base_url / api_key / model）
  profile.md    你的私人信息（会被塞进提示词，别放敏感信息）
  lines.yaml    预生成台词池（所有角色合在一个文件里，界面可改）
  ui.json       界面偏好（音色 / 形象 / 各种开关）

需要你自己放的东西（程序**不代为分发**，都有版权）
--------------------------------------------------
  core/                    Live2D 官方的 Cubism Core（自己从官网下载、同意条款）
  l2d/                     渲染外壳的打包产物（跑 scripts/build_live2d.sh 生成）
  voices/<音色 id>/         音色模型（*.pth / *.index / voice.yaml）
  characters/<角色 id>/     角色素材（Live2D 模型 / character.yaml / persona.md）

备份建议
--------
这个目录**不在任何 git 仓库里** —— 换电脑或重装前请自己拷走。
"""


def ensure_user_workspace() -> Dict[str, Any]:
    """确保用户工作区存在；**只创建、绝不覆盖** ✗。

    返回 ``{"dir": ..., "created": [...], "missing": [...]}`` ——
    调用方可以把它打进日志 / 活动记录，让"这次建了什么"看得见 ✅。
    """

    from nekopal.packs import config_dir

    root = config_dir()
    created: List[str] = []

    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 - 建不出来也不该让程序起不来
        return {"dir": str(root), "created": [], "missing": [], "error": "无法创建配置目录"}

    from nekopal.chat import IDLE_PROMPT_TEMPLATE

    templates = {
        "chat.yaml": CHAT_TEMPLATE,
        "profile.md": PROFILE_TEMPLATE,
        "README.txt": README_TEXT,
        "idle_prompt.md": IDLE_PROMPT_TEMPLATE,
    }
    for name, body in templates.items():
        path = root / name
        if path.exists():
            continue  # ⚠️ 已存在就一个字都不动 ✗
        try:
            path.write_text(body, encoding="utf-8")
            if name == "chat.yaml":
                path.chmod(0o600)  # 里面将来会有 key
            created.append(name)
        except Exception:  # noqa: BLE001
            pass

    # ui.json：只写一份空的默认值（键都在 defaults 里，`_load_ui` 会合并）
    ui = root / "ui.json"
    if not ui.exists():
        try:
            ui.write_text(json.dumps({}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            created.append("ui.json")
        except Exception:  # noqa: BLE001
            pass

    migrated = _migrate_ui_voices(root)

    # 这些是**需要用户自己放**的（有版权，程序不分发）
    missing = [
        name
        for name in ("core", "voices", "characters")
        if not (root / name).exists()
    ]

    return {"dir": str(root), "created": created, "missing": missing, "migrated": migrated}


def _migrate_ui_voices(root: Path) -> Optional[str]:
    """把旧的**全局音色**落到"当前这个角色"名下（R26 · 一次性迁移）。

    **为什么必须做**：音色从"全局一个值"改成"每个角色一个值"之后 ✗，
    角色包里那句 `voice: rana` 会**第一次真的生效**（以前它一直被全局值盖着 ✗）。
    如果角色包声明的是另一个音色 ✗，用户明明选过的就会被**悄悄改掉** ✗。

    ⇒ 升级时**先把旧值记到当前角色名下** ✅ —— 用户的明确选择永远优先 ✗。

    ⚠️ 只做三件事：读、加一个键、写回。**不删任何键** ✗。
       已经迁移过（`voices` 里已有当前角色）就原样返回 ✗。
    """

    path = root / "ui.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - 读不动就别动它
        return None
    if not isinstance(data, dict):
        return None

    legacy = str(data.get("voice") or "").strip()
    character = str(data.get("character") or "").strip()
    voices = data.get("voices")
    if not isinstance(voices, dict):
        voices = {}
    if not legacy or not character or voices.get(character):
        return None

    voices[character] = legacy
    data["voices"] = voices
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        return None
    return "{} → voices[{}]".format(legacy, character)
