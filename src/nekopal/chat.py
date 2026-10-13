"""角色「聊天」：让大模型按**角色性格 + 你的私人信息**生成一句短台词，再交给 TTS。

## 三个接口位（都是**文件**，不用改代码 ✗）

```
~/.config/nekopal/chat.yaml                    LLM 配置：base_url / api_key / model
~/.config/nekopal/profile.md                   你的私人信息（称呼、近况、在意的事…）
~/.config/nekopal/characters/<id>/persona.md    角色性格（没有就退回仓库里的示例 ✗）
```

## 红线（这一节比代码重要 ✗）

- **api_key 只存在 `chat.yaml` 里** ✗ —— 本模块**永不**打印它 ✗、
  **永不**通过接口返回它 ✗（状态接口只回 `has_key: true/false` ✗）、
  也**永不**进仓库 ✗
- **`profile.md` 是私人信息** ✗ —— 同样只在你的工作区里 ✗，仓库里没有它 ✗
- **没配 LLM 时不当成错误** ✗ —— 自动退回角色包里的静态台词表 ✅
  （所以"不配也能用"✗，只是台词是固定的几句 ✗）

## 为什么用标准库而不是 `openai` 包 ✗

这个项目的依赖一直是**刻意最小**的 ✗ —— 能不加就不加 ✗。
OpenAI 兼容的 `/chat/completions` 本质就是一个 JSON POST ✗，`urllib` 足够 ✅
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "chat_file",
    "default_config",
    "is_enabled",
    "load_config",
    "load_persona",
    "load_profile",
    "profile_file",
    "public_status",
    "save_config",
    "generate_line",
]

#: 生成的台词硬上限（长了 TTS 慢、也不像"随口一句" ✗）
MAX_LINE_CHARS = 30

#: 给模型的硬要求 ✗ —— 风格几乎完全由它和 persona 决定 ✗
_SYSTEM_TEMPLATE = """你正在扮演一个角色，随机说一句**很短**的中文口语。

## 你的角色设定
{persona}

## 关于对话对象（用来让话题更贴近，不要复述、不要提到"资料"）
{profile}

## 硬要求
- 只输出**一句话**，不超过 {limit} 个字
- 像随口搭话，不要解释、不要提问式客套、不要旁白
- **不要**加引号、书名号、括号、emoji、颜文字
- 不要提到"设定""角色""扮演"这些词
"""


def chat_file() -> Path:
    from nekopal.packs import config_dir

    return config_dir() / "chat.yaml"


def profile_file() -> Path:
    from nekopal.packs import config_dir

    return config_dir() / "profile.md"


def default_config() -> Dict[str, Any]:
    return {
        #: OpenAI 兼容的根地址（不带 /chat/completions ✗）
        "base_url": "",
        #: ⚠️ 只存在这个文件里 ✗（仓库外 ✗、不进日志 ✗）
        "api_key": "",
        "model": "",
        "temperature": 1.0,
        "max_tokens": 60,
        "timeout": 20,
    }


def load_config() -> Dict[str, Any]:
    """读 `chat.yaml`（没有 / 坏了 → 一份空配置 ✗）。"""

    cfg = default_config()
    try:
        import yaml

        data = yaml.safe_load(chat_file().read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            for k in cfg:
                if k in data and data[k] is not None:
                    cfg[k] = data[k]
    except Exception:  # noqa: BLE001 - 没有配置是**正常状态** ✗
        pass
    return cfg


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并式写入（空字符串 = 清掉该项 ✗）。"""

    cfg = load_config()
    for k, v in (patch or {}).items():
        if k in cfg and v is not None:
            cfg[k] = v
    try:
        import yaml

        chat_file().parent.mkdir(parents=True, exist_ok=True)
        chat_file().write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        chat_file().chmod(0o600)  # 里面有 key ✗ —— 只给本人读写
    except Exception:  # noqa: BLE001 - 写不进去也不该让请求失败
        pass
    return cfg


def is_enabled() -> bool:
    """配好 LLM 了吗（base_url + api_key + model 三样都要 ✗）。"""

    c = load_config()
    return bool(str(c.get("base_url") or "").strip()
                and str(c.get("api_key") or "").strip()
                and str(c.get("model") or "").strip())


def load_profile() -> str:
    """读私人信息文档 ✗（仓库外 ✗）。没有就返回空 ✗。"""

    try:
        return profile_file().read_text(encoding="utf-8").strip()[:4000]
    except Exception:  # noqa: BLE001
        return ""


def load_persona(character_id: Optional[str]) -> str:
    """读角色性格文档 ✗。

    顺序：**你的工作区** → **仓库里的示例** ✗
    （所以不改也能跑 ✅，但仓库里那份只是示例 ✗）
    """

    from nekopal.packs import config_dir, repo_root

    if character_id:
        mine = config_dir() / "characters" / character_id / "persona.md"
        try:
            text = mine.read_text(encoding="utf-8").strip()
            if text:
                return text[:4000]
        except Exception:  # noqa: BLE001
            pass

        for base in (repo_root() / "characters", config_dir() / "characters"):
            cand = base / "example-{}".format(character_id) / "persona.md"
            try:
                text = cand.read_text(encoding="utf-8").strip()
                if text:
                    return text[:4000]
            except Exception:  # noqa: BLE001
                continue

    return "（没有角色设定，就正常、简短地说一句中文口语即可）"


def public_status() -> Dict[str, Any]:
    """给前端看的状态 ✗ —— **绝不包含 api_key** ✗。"""

    c = load_config()
    profile = load_profile()
    return {
        "enabled": is_enabled(),
        "base_url": str(c.get("base_url") or ""),
        "model": str(c.get("model") or ""),
        # ⚠️ 只回"有没有"✗，不回内容 ✗
        "has_key": bool(str(c.get("api_key") or "").strip()),
        "has_profile": bool(profile),
        "profile_chars": len(profile),
        "chat_file": str(chat_file()),
        "profile_file": str(profile_file()),
    }


def _strip_line(raw: str) -> str:
    """把模型输出收拾成"一句干净的口语"✗。

    模型很爱加引号 / 换行 / 括号注解 ✗ —— 这些交给 TTS 会读出来 ✗，必须清掉 ✅
    """

    line = (raw or "").strip()
    # 只取第一行（有的模型会补一句解释 ✗）
    for sep in ("\n", "。"):
        if sep in line:
            head = line.split(sep)[0].strip()
            if head:
                line = head
                break
    # 去掉成对的引号 / 书名号 / 括号
    pairs = [('"', '"'), ("'", "'"), ("「", "」"), ("“", "”"), ("『", "』"),
             ("（", "）"), ("(", ")"), ("[", "]")]
    for a, b in pairs:
        if line.startswith(a) and line.endswith(b) and len(line) > 2:
            line = line[1:-1].strip()
    return line[:MAX_LINE_CHARS].strip()


def generate_line(character_id: Optional[str], scene: str = "") -> Dict[str, Any]:
    """让模型生成一句台词 ✗。**失败一律抛异常**✗，由调用方决定退回静态台词 ✅。"""

    cfg = load_config()
    if not is_enabled():
        raise RuntimeError("没配 LLM（chat.yaml 里缺 base_url / api_key / model）")

    persona = load_persona(character_id)
    profile = load_profile()
    system = _SYSTEM_TEMPLATE.format(
        persona=persona,
        profile=profile or "（没有额外信息，就正常说一句 ✗）",
        limit=MAX_LINE_CHARS,
    )
    user = scene or "（对方点了一下你，随口说一句）"

    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": str(cfg["model"]),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(cfg.get("temperature") or 1.0),
            "max_tokens": int(cfg.get("max_tokens") or 60),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # ⚠️ key 只在这一行出现 ✗ —— 出错时也**不能**把它带进异常信息 ✗
            "Authorization": "Bearer {}".format(str(cfg["api_key"])),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=float(cfg.get("timeout") or 20)
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # ⚠️ 只回状态码 ✗ —— HTTPError 的 str() 里可能带 URL（不含 key ✗），
        #    但为了保险，这里自己拼一句话 ✗
        raise RuntimeError("LLM 返回 HTTP {}".format(exc.code)) from None
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("调 LLM 失败：{}".format(type(exc).__name__)) from None

    try:
        raw = payload["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        raise RuntimeError("LLM 返回格式不对") from None

    line = _strip_line(str(raw))
    if not line:
        raise RuntimeError("LLM 没说出有效内容")
    return {"text": line, "model": str(cfg["model"])}
