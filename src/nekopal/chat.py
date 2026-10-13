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
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "DEFAULT_BATCH",
    "chat_file",
    "clear_lines",
    "default_config",
    "generate_line",
    "generate_lines",
    "is_enabled",
    "lines_file",
    "lines_status",
    "load_config",
    "load_lines",
    "load_persona",
    "load_profile",
    "pick_line",
    "profile_file",
    "public_status",
    "save_config",
    "save_lines",
]

#: 生成的台词硬上限（长了 TTS 慢、也不像"随口一句" ✗）
MAX_LINE_CHARS = 30

#: 「预生成一批」默认条数 ✗
DEFAULT_BATCH = 30

#: 一次预生成的最大条数 ✗（再多意义不大，还容易跑偏 ✗）
MAX_BATCH = 80

#: 给模型的硬要求 ✗ —— 风格几乎完全由它和 persona 决定 ✗
_SYSTEM_TEMPLATE = """你正在扮演一个角色，说一句**很短**的中文口语。

## 你的角色设定
{persona}

## 关于和你说话的人（**她希望你多知道一点她的事**）
{profile}

## 硬要求
- 只输出**一句话**，不超过 {limit} 个字
- 像随口搭话：不要解释、不要提问式客套、不要旁白、不要打气式安慰
- **不要**加引号、书名号、括号、emoji、颜文字
- 不要提到"设定""角色""扮演""资料"

## 说什么（**最重要的一条**）
- 她的称呼就写在上面，**但别每句都只叫一声称呼** —— 那等于什么都不知道
- 从这两类里挑一类说：
  · **她的近况**：在上的课 / 在自学的东西 / 在做的那个项目 /
    找工作看的地方 / 她喜欢的变化和出差 / 她跟家里的拉扯（这条点到为止）
    用**你自己的语气**点一句 —— **不是复述资料，也不是替她分析**
  · **你的招牌**：人设里那些口头禅与在意的事
- 具体到她的事时，**宁可小、宁可具体**：
  「上海那边，还没消息。」胜过「加油找工作。」

## 语气（别踩的坑）
- **最多每 4 句里有 1 句**用「……」开口 —— 每句都带就变成叹气了
- **不要叹词**：唉、唔、呼——、哈、啧、啊
- 语气是**平**，不是**丧**
"""

#: 一次生成一整批（预生成台词池 ✗）用的提示词。
#: 和单句版的区别：**明确要求配比** ✗，并且**盯着「……」和叹词** ✗。
_BATCH_TEMPLATE = """你正在扮演一个角色。下面是你的人设：

{persona}

## 关于和你说话的人（**重点：她希望你多少知道一点她的事**）
{profile}

## 任务
写 {count} 句**很短**的中文口语，都是你对她说过 / 会说的话。

## 输出格式（严格遵守）
- **一句一行**，正好 {count} 行
- **不要**编号、不要引号、不要书名号、不要括号、不要任何解释或标题
- 每句 **3～14 个字**

## 这 {count} 句的配比（重要）
- **大约 4 成**要**具体到她的事**：她的课 / 课题组 / 在自学的东西 /
  她正在做的那个项目 / 找工作的城市与方向 / 她喜欢变化和出差 / 她和家里的拉扯
  —— 用**你自己的语气**点一句，**点到为止**（不是复述资料，不是替她出主意）
  · 例子：「上海那边，还没消息。」「又是写代码。」「那五门课，念完了吗。」
- **大约 3 成**是**你的招牌**：人设里的口头禅、抹茶、猫、突然冒出来的一句直白话
- **剩下 3 成**是**当下气氛**：你在、你在听、你注意到她了

## 语气（别踩的坑）
- **整批 {count} 句里，带「……」开头的不要超过 4 句** ——
  她确实寡言，但**每句都叹气就不是她了**。大多数句子直接开口说。
- **不要叹词**：唉、唔、呼——、哈、啧、啊、呜
- 语气是**平**，不是**丧**
- 不要 emoji、不要颜文字、不要"哈哈"
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


def _current_character() -> str:
    """当前选的角色 id ✗（读 `ui.json` ✗；读不到 → 空 ✗）。"""

    try:
        from nekopal.packs import config_dir

        data = json.loads((config_dir() / "ui.json").read_text(encoding="utf-8"))
        return str((data or {}).get("character") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def public_status() -> Dict[str, Any]:
    """给前端看的状态 ✗ —— **绝不包含 api_key** ✗。"""

    c = load_config()
    profile = load_profile()
    cid = _current_character() or None
    pool = load_lines(cid)
    return {
        "enabled": is_enabled(),
        "base_url": str(c.get("base_url") or ""),
        "model": str(c.get("model") or ""),
        # ⚠️ 只回"有没有"✗，不回内容 ✗
        "has_key": bool(str(c.get("api_key") or "").strip()),
        "has_profile": bool(profile),
        "profile_chars": len(profile),
        # 性格文档填了没 ✗ —— 只关心**你自己的那份**（仓库里的示例不算 ✗）
        "has_persona": _own_persona_exists(),
        "chat_file": str(chat_file()),
        "profile_file": str(profile_file()),
        # 预生成台词池 ✗
        "character": cid or "",
        "lines_file": pool["file"],
        "lines_count": pool["count"],
        "lines_stale": pool["stale"],
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


def _post_chat(system: str, user: str, max_tokens: int) -> str:
    """调一次 OpenAI 兼容的 `/chat/completions`，返回正文 ✗。

    ⚠️ **异常信息里绝不能带 api_key** ✗ —— 所以 HTTPError 只取状态码 ✗，
    其余异常只取异常**类名**✗（`str(exc)` 有可能把请求头带出来 ✗）。
    """

    cfg = load_config()
    if not is_enabled():
        raise RuntimeError("没配 LLM（chat.yaml 里缺 base_url / api_key / model）")

    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": str(cfg["model"]),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(cfg.get("temperature") or 1.0),
            "max_tokens": int(max_tokens),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # ⚠️ key 只在这一行出现 ✗
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
        raise RuntimeError("LLM 返回 HTTP {}".format(exc.code)) from None
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("调 LLM 失败：{}".format(type(exc).__name__)) from None

    try:
        return str(payload["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        raise RuntimeError("LLM 返回格式不对") from None


def generate_line(character_id: Optional[str], scene: str = "") -> Dict[str, Any]:
    """让模型生成一句台词 ✗。**失败一律抛异常**✗，由调用方决定退回静态台词 ✅。"""

    cfg = load_config()
    system = _SYSTEM_TEMPLATE.format(
        persona=load_persona(character_id),
        profile=load_profile() or "（没有额外信息，就正常说一句 ✗）",
        limit=MAX_LINE_CHARS,
    )
    raw = _post_chat(
        system,
        scene or "（对方点了一下你，随口说一句）",
        int(cfg.get("max_tokens") or 60),
    )
    line = _strip_line(raw)
    if not line:
        raise RuntimeError("LLM 没说出有效内容")
    return {"text": line, "model": str(cfg["model"])}


# -- 预生成台词池 ---------------------------------------------------------
# 为什么有这套 ✗：`/api/avatar/chat` 每次点击都打一次 LLM ✗，一次要好几秒 ✗
# —— 点一下要等，完全不像"随口一句" ✗。
# 改成**一次生成几十条存档** ✗，点击时只从池子里抽 ✅（零延迟 ✗）。
#
# ⚠️ 池子存在**仓库外**✗：它是 profile.md（私人信息）的衍生物 ✗
#    → `~/.config/nekopal/characters/<id>/lines.json`


def lines_file(character_id: Optional[str]) -> Path:
    from nekopal.packs import config_dir

    cid = str(character_id or "").strip() or "default"
    return config_dir() / "characters" / cid / "lines.json"


def _fingerprint(character_id: Optional[str]) -> str:
    """给「人设 + 私人信息」算个短指纹 ✗。

    用来判断池子"过时了没" ✗ —— 你改了 profile.md 或 persona.md ✗，
    旧台词就该提示重新生成 ✅（不是错误状态 ✗）。
    """

    import hashlib

    blob = "{}\n{}".format(load_persona(character_id), load_profile())
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def load_lines(character_id: Optional[str]) -> Dict[str, Any]:
    """读台词池 ✗（没有 → 空池 ✗，不算错误 ✗）。"""

    path = lines_file(character_id)
    empty: Dict[str, Any] = {
        "lines": [],
        "count": 0,
        "generated_at": None,
        "model": "",
        "stale": False,
        "file": str(path),
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return empty
    if not isinstance(data, dict):
        return empty

    lines = [str(x).strip() for x in (data.get("lines") or []) if str(x).strip()]
    fp = str(data.get("fingerprint") or "")
    return {
        "lines": lines,
        "count": len(lines),
        "generated_at": data.get("generated_at"),
        "model": str(data.get("model") or ""),
        # 指纹对不上 = 人设/私人信息改过了 ✗ → 池子还可用，但建议重生成
        "stale": bool(fp) and fp != _fingerprint(character_id),
        "file": str(path),
    }


def save_lines(
    character_id: Optional[str], lines: List[str], model: str = ""
) -> Dict[str, Any]:
    """写入台词池 ✗（顺手记下生成时间 / 模型 / 指纹 ✗）。"""

    import time

    # ⚠️ 界面上「改字 / 删句」走的是 PUT ✗ —— 它不知道该填什么模型 ✗。
    #    不保留的话，一次编辑就把生成模型抹成空 ✗（界面上显示成 `?` ✗）。
    if not model:
        model = str(load_lines(character_id).get("model") or "")

    payload = {
        "generated_at": time.time(),
        "model": model,
        "fingerprint": _fingerprint(character_id),
        "lines": [str(x).strip()[:MAX_LINE_CHARS] for x in lines if str(x).strip()],
    }
    path = lines_file(character_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - 写不进去也要把内存里的结果给前端 ✅
        pass
    got = load_lines(character_id)
    # 写失败时 load 会读到空的 ✗ → 那就直接把内存里这份还回去 ✅
    if not got["lines"] and payload["lines"]:
        got["lines"] = payload["lines"]
        got["count"] = len(payload["lines"])
    return got


def clear_lines(character_id: Optional[str]) -> Dict[str, Any]:
    """清空台词池 ✗。"""

    try:
        lines_file(character_id).unlink()
    except Exception:  # noqa: BLE001
        pass
    _BAGS.pop(str(character_id or ""), None)
    return load_lines(character_id)


def _clean_batch(raw: str, want: int) -> List[str]:
    """把模型那一大坨输出收拾成一行一句 ✗。

    模型很爱自作主张 ✗：编号、加粗、引号、还常常多给几句 ✅ ——
    所以这里**去编号、去引号、去重、截断**✗，而不是相信它守格式 ✗。
    """

    out: List[str] = []
    for chunk in re.split(r"[\r\n]+", raw or ""):
        s = chunk.strip()
        if not s:
            continue
        # 去编号：`1. ` / `1、` / `1)` / `- ` / `* ` / `• `
        s = re.sub(r"^\s*(?:[-*•·]|\(?\d{1,2}[.、)）:：])\s*", "", s).strip()
        # 去成对包裹的引号 / 书名号 / 括号（只去一层 ✗）
        s = re.sub(r'^["\'「『“”‘’\(（\[【]+', "", s)
        s = re.sub(r'["\'」』“”‘’\)）\]】]+$', "", s).strip()
        # 去 markdown 强调
        s = s.replace("**", "").strip()
        if not s or len(s) > 40:
            continue
        out.append(s[:MAX_LINE_CHARS])

    seen: set = set()
    uniq: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq[:want]


def generate_lines(
    character_id: Optional[str], count: int = DEFAULT_BATCH
) -> Dict[str, Any]:
    """**一次生成一整批**台词并存档 ✗。失败抛异常 ✗。

    一次调用出几十条 ✗ —— 比"点 30 次各生成一句"省 29 次往返 ✗，
    而且**模型能看到全局**✗，配比（人设 / 她的近况 / 气氛）更像人 ✗。
    """

    cfg = load_config()
    if not is_enabled():
        raise RuntimeError("没配 LLM（chat.yaml 里缺 base_url / api_key / model）")

    try:
        want = int(count)
    except (TypeError, ValueError):
        want = DEFAULT_BATCH
    want = max(5, min(want, MAX_BATCH))

    system = _BATCH_TEMPLATE.format(
        persona=load_persona(character_id),
        profile=load_profile() or "（没有额外信息 ✗）",
        count=want,
    )
    raw = _post_chat(
        system,
        "直接按格式输出那 {} 行 ✗。".format(want),
        # 思考型模型的 reasoning 也吃 max_tokens ✗ → 给宽一点 ✅
        max(1500, want * 120),
    )
    lines = _clean_batch(raw, want)
    if len(lines) < 3:
        raise RuntimeError("模型没给出足够多的有效台词（只拿到 {} 条 ✗）".format(len(lines)))
    return save_lines(character_id, lines, str(cfg["model"]))


#: 「抽签袋」——保证一轮之内**不重复**✗（抽空了自动重填 ✅）。
#: 只在内存里 ✗：进程重启就重来 ✅（不值得为它落盘 ✗）。
_BAGS: Dict[str, List[str]] = {}


def pick_line(
    character_id: Optional[str], lines: Optional[List[str]] = None
) -> Optional[str]:
    """从池子里抽一句 ✗（一轮内不重复 ✗）。池子空 → `None` ✗。"""

    import random

    pool = [str(x).strip() for x in (lines if lines is not None
                                     else load_lines(character_id)["lines"]) if str(x).strip()]
    if not pool:
        return None

    key = str(character_id or "")
    # 池子被改过（重新生成 / 手动编辑 ✗）→ 袋子里那些不在新池子里的就丢掉 ✗
    bag = [x for x in _BAGS.get(key, []) if x in pool]
    if not bag:
        bag = pool[:]
        random.shuffle(bag)
    line = bag.pop()
    _BAGS[key] = bag
    return line


def lines_status(character_id: Optional[str]) -> Dict[str, Any]:
    """池子现状 + 能不能生成 ✗（给界面看 ✗）。"""

    data = load_lines(character_id)
    data["enabled"] = is_enabled()
    data["remaining"] = len(_BAGS.get(str(character_id or ""), []))
    data["default_batch"] = DEFAULT_BATCH
    data["max_batch"] = MAX_BATCH
    return data


def _own_persona_exists() -> bool:
    """**工作区里**那份 persona.md 存在吗 ✗（仓库里的示例不算 ✗）。"""

    cid = _current_character()
    if not cid:
        return False
    try:
        from nekopal.packs import config_dir

        return (config_dir() / "characters" / cid / "persona.md").is_file()
    except Exception:  # noqa: BLE001
        return False
