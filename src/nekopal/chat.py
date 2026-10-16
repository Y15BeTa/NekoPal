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
    "IDLE_PROMPT_TEMPLATE",
    "generate_idle",
    "idle_prompt_file",
    "load_idle_prompt",
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
    "merge_lines",
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

## 这次她特别要求的（**最高优先，尽量满足**）
{want}

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

#: 用户没写特别要求时，塞进 `{want}` 的占位 ✗
_NO_WANT = "（没写特别要求 —— 就按下面的配比来）"


#: `chat.yaml` 解析失败的原因 ✗（没问题 = `None` ✅）。
#: 为什么要有它：坏了以后所有 AI 功能会**静默失效** ✗，界面上必须看得见 ✗。
_CONFIG_ERROR: Optional[str] = None


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
    """读 `chat.yaml`（没有 / 坏了 → 一份空配置 ✗）。

    ⚠️ **坏了必须记下来** ✗ —— 早期版本静默吞掉异常 ✗，
    结果 `is_enabled()` 变 `False` ✗、AI 整条路被跳过 ✗，
    而用户**界面上只看到"台词变回那几句没关系的"** ✗，完全查不出原因 ✗
    （2026-10-14 真实踩到 ✗：`chat.yaml` 被编坏，YAML 解析失败 ✗）。
    所以现在把错误存进 `_CONFIG_ERROR` ✗，由 `public_status()` 带给界面 ✅。
    """

    global _CONFIG_ERROR

    cfg = default_config()
    if not chat_file().is_file():
        _CONFIG_ERROR = None  # 没配是**正常状态** ✗，不是错误 ✗
        return cfg
    try:
        import yaml

        data = yaml.safe_load(chat_file().read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            for k in cfg:
                if k in data and data[k] is not None:
                    cfg[k] = data[k]
            _CONFIG_ERROR = None
        else:
            _CONFIG_ERROR = "chat.yaml 的顶层不是键值对"
    except Exception as exc:  # noqa: BLE001
        # 只留一行、不留文件内容（里面可能有 key ✗）
        head = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        _CONFIG_ERROR = "{}（{}）".format(type(exc).__name__, head[:100])
    return cfg


def config_error() -> Optional[str]:
    """`chat.yaml` 有问题吗 ✗（没问题 → `None` ✅）。"""

    load_config()
    return _CONFIG_ERROR


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
        # ⚠️ 配置坏没坏 ✗ —— 坏了的话 AI 会静默失效，界面必须显示出来 ✗
        "config_error": config_error(),
        # 预生成台词池 ✗
        "character": cid or "",
        "lines_file": pool["file"],
        "lines_count": pool["count"],
        "lines_stale": pool["stale"],
    }


def _strip_line(raw: str) -> str:
    """把模型输出收拾成"一句干净的口语"✗。

    模型很爱加引号 / 换行 / 括号注解 ✗ —— 这些交给 TTS 会读出来 ✗，必须清掉 ✅

    ⚠️ 两处顺序很讲究（2026-10-14 被单测抓出来 ✗）：

    1. **先剥引号、再切句** ✗ —— 反过来的话 `「在的。」` 会被 `。` 切成
       `「在的` ✗，那个孤零零的左括号**会被念出来** ✗。
    2. **切句要保留句号** ✗ —— 老实现一律在第一个 `。` 处切掉 ✗，
       于是"现编"的台词永远不带句号 ✗，而"预生成池"里的却都带 ✗ ——
       两条路的输出**不一致** ✗。
    """

    line = (raw or "").strip()
    if not line:
        return ""

    # 只取第一行（有的模型会补一句解释 ✗）
    line = line.splitlines()[0].strip()

    # ① 先去成对的引号 / 书名号 / 括号（**必须在切句之前** ✗）
    pairs = [('"', '"'), ("'", "'"), ("「", "」"), ("“", "”"), ("『", "』"),
             ("（", "）"), ("(", ")"), ("[", "]")]
    for a, b in pairs:
        if line.startswith(a) and line.endswith(b) and len(line) > 2:
            line = line[1:-1].strip()
            break

    # ② 还带着多个句子 → 只留第一句（**连句号一起留** ✗）
    for sep in ("。", "！", "？", "!", "?", "."):
        # 末尾那个句号不算"还有下一句" ✗
        if sep in line[:-1]:
            head = line[: line.index(sep) + 1].strip()
            if head:
                line = head
                break

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


# -- 挂机时"自发说一句"（R16）--------------------------------------------
# 用户的原话：「挂机偶尔突然实时生成语音：根据角色人设 + 私人信息 +
# **近期读的 opencode 文字** + 其他内容（近期新闻 / 早安午安晚安 /
# 快睡觉多喝水 / 快写作业 / 别玩手机等）……」
#
# 他自己就点出了关键：**不作为点击反馈**（太慢、体验不好），
# 而是**偶尔自发**触发 ✅。所以这条走的是"慢一点没关系"的路 ——
# 可以打一次大模型、可以等两秒，只要**别打断用户**，也**别在半夜出声**。

_IDLE_TEMPLATE = """你正在扮演一个角色。下面是你的人设：

{persona}

## 关于和你说话的人
{profile}

## 现在是
{moment}

## 她最近在读 / 在看的文字（**只当话题线索用，不要复述内容**）
{recent}

## 额外要求（她自己写的，优先满足）
{extra}

## 任务
她**没有在跟你说话** —— 你只是**自己突然想到一句话**，说给她听。
（所以不要"在吗""怎么了"这种回应式的话）

## 硬要求
- 只输出**一句话**，**3～14 个字**，像自言自语
- 不要引号、书名号、括号、emoji、颜文字
- 不要提到"设定""角色""资料""最近"
- 不要叹气、不要写「唉」「唔」「呼」
- 如果上面给了"她最近在读的文字"，**至多点一件**，点到为止
"""

#: `idle_prompt.md` 的模板（**仓库外** ✗，用户可以自己改 ✗）
IDLE_PROMPT_TEMPLATE = """# 挂机时"自发说一句"的额外要求

> 这份文档会被**塞进提示词**，只在**挂机自发生成**时用到
> （点击反馈走的是 persona.md，不用这份）。
>
> 写什么都可以：想让她多提什么、少提什么、什么时段说什么。
> 留空 = 不加额外要求（也能用）。

## 例子（可以照抄改）

- 早上可以说一句早安；晚上 11 点后提醒我去睡觉
- 偶尔提醒我喝水、别一直玩手机、作业还没写
- 多提一点我在上的课和找工作的进度
- 别总提抹茶了，最近有点腻

## 我的要求

（在这里写）
"""


def idle_prompt_file() -> Path:
    from nekopal.packs import config_dir

    return config_dir() / "idle_prompt.md"


def load_idle_prompt() -> str:
    """读用户写的「挂机额外要求」✗（没有 → 空 ✗）。"""

    try:
        return idle_prompt_file().read_text(encoding="utf-8").strip()[:2000]
    except Exception:  # noqa: BLE001
        return ""


def generate_idle(
    character_id: Optional[str],
    recent: Optional[List[str]] = None,
    moment: str = "",
) -> Dict[str, Any]:
    """为「挂机自发一句」生成台词 ✗。失败抛异常 ✗。"""

    cfg = load_config()
    if not is_enabled():
        raise RuntimeError("没配 LLM（chat.yaml 里缺 base_url / api_key / model）")

    extra = load_idle_prompt()
    # 模板里那段"例子"只是给人看的 —— 送进提示词时去掉，别让模型照着念 ✗
    if "## 我的要求" in extra:
        extra = extra.split("## 我的要求", 1)[1].strip()

    recent_text = "（这次没有）"
    if recent:
        # ⚠️ 只给**片段**、只给最近几条 ✗ —— 既省 token，也避免把整篇发出去 ✗
        bits = ["· " + t.strip().replace("\n", " ")[:60] for t in recent[-3:] if t.strip()]
        if bits:
            recent_text = "\n".join(bits)

    system = _IDLE_TEMPLATE.format(
        persona=load_persona(character_id),
        profile=load_profile() or "（没有额外信息）",
        moment=moment or "（不知道，别提具体时间）",
        recent=recent_text,
        extra=extra or "（没有）",
    )
    raw = _post_chat(system, "说一句吧。", max(300, int(cfg.get("max_tokens") or 400)))
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


def lines_file() -> Path:
    """**所有角色的台词池都在这一个文件里** ✗（方便你直接打开改 ✗）。

    为什么从「每个角色一份 `characters/<id>/lines.json`」改成
    「一份 `lines.yaml`」✗：用户明确要求"把我们所有存下来的预设的池子
    都放在一个统一的个人设置文件里面"✗ ——
    而且 YAML 比 JSON **好手工编辑**✗（不用管逗号引号 ✗）。

    结构：

    ```yaml
    characters:
      rana:
        generated_at: 1791907107.8
        model: deepseek-flash
        want: "多一些跟找工作有关的"      # 你那次生成时写的一句话需求
        lines:
          - 那五门课，念完了吗。
          - 有趣的女人。
    ```
    """

    from nekopal.packs import config_dir

    return config_dir() / "lines.yaml"


def _fingerprint(character_id: Optional[str]) -> str:
    """给「人设 + 私人信息」算个短指纹 ✗。

    用来判断池子"过时了没" ✗ —— 你改了 `profile.md` 或 `persona.md` ✗，
    旧台词就该提示重新生成 ✅（**不是错误状态** ✗，旧台词照样能用 ✅）。
    """

    import hashlib

    blob = "{}\n{}".format(load_persona(character_id), load_profile())
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _legacy_lines_file(character_id: Optional[str]) -> Path:
    """老位置（每个角色一份 `characters/<id>/lines.json` ✗）—— 只用于一次性迁移 ✗。"""

    from nekopal.packs import config_dir

    cid = str(character_id or "").strip() or "default"
    return config_dir() / "characters" / cid / "lines.json"


def _drop_legacy_lines(character_id: Optional[str]) -> None:
    """搬完就把老文件**删掉**。

    ⚠️ 这里曾经踩过：`_migrate_legacy()` **只搬不删** ✗，而删除只写在 `clear_lines()` 里 ✗
    → 于是老文件**永远留着**（一个 30 条的冻结副本），看起来像"台词有两个地方、
    不知道哪个算数"✗ —— 其实代码里很清楚：**统一文件才算数**，
    老文件是**没人读**的残留 ✗。

    ⚠️ **必须在写入成功之后才删** ✗ —— 先删再写、而写又失败的话，就真丢台词了 ✗。
    """

    try:
        _legacy_lines_file(character_id).unlink()
    except Exception:  # noqa: BLE001 - 删不掉不该影响"读台词"这件事
        pass


#: 上一次读 `lines.yaml` 时的**解析错误**（None = 正常 ✗）。
#:
#: ⚠️ 为什么需要它：2026-10-17 那个文件被写坏过 ✗（`lines` 丢了一个冒号 ✗）→
#: YAML 解析失败 ✗ → 而这里**静默吞掉异常、当成空池子** ✗ →
#: 界面上表现成"**台词池凭空消失**"✗（用户只会觉得"我存的台词没了"✗）。
#: **文件坏了是个错误状态** ✗ 不该和"还没生成过"长得一模一样 ✗ ——
#: 所以把错误记下来，让 `/api/lines` 能报出去 ✅。
_LAST_POOL_ERROR: Optional[str] = None


def _read_pool_file() -> Dict[str, Any]:
    global _LAST_POOL_ERROR
    try:
        import yaml

        data = yaml.safe_load(lines_file().read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        _LAST_POOL_ERROR = None  # 还没建过 = 正常 ✗ 不是错 ✗
        return {}
    except Exception as exc:  # noqa: BLE001
        # ⚠️ 文件**存在但读不动** ✗ —— 这才是要报警的那种 ✗
        _LAST_POOL_ERROR = "{}: {}".format(type(exc).__name__, exc)
        return {}
    _LAST_POOL_ERROR = None
    if not isinstance(data, dict):
        _LAST_POOL_ERROR = "顶层不是字典（{}）".format(type(data).__name__)
        return {}
    chars = data.get("characters")
    return chars if isinstance(chars, dict) else {}


def _write_pool_file(chars: Dict[str, Any]) -> None:
    import yaml

    from nekopal.fileutil import write_text_atomic

    path = lines_file()
    body = (
        "# NekoPal 预生成台词池\n"
        "#\n"
        "# ⚠️ 这个文件在**仓库外**（它是 profile.md 的衍生物 ✗）—— 别提交、别外发。\n"
        "# 想改台词：直接改下面的 lines 就行（改完在界面上点一下「刷新」即可生效 ✗）。\n"
        "# 想加一句自己的：在 lines 里新起一行、前面加 `- ` 就行。\n"
        "#\n"
        "# 每个角色一节；`want` 是那次生成时写的一句话需求（只是记录 ✗，不影响运行）。\n"
        "\n"
        + yaml.safe_dump({"characters": chars}, allow_unicode=True, sort_keys=False)
    )
    # ⚠️ **必须原子写** ✗ —— 以前是 `path.write_text()` ✗（open 截断 → write ✗ 无保护 ✗）
    # 2026-10-17 就因此丢掉过一个冒号 ✗ → YAML 解析失败 ✗ → 而 `load_lines` 把异常
    # 当成"空池子"吞掉 ✗ → 界面上表现成"**台词池凭空消失**"✗（静默数据丢失 ✗）。
    write_text_atomic(path, body, mode=0o600)  # 里面是私人语境衍生物 ✗ 只给本人读写


def _migrate_legacy(character_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """把老的 `characters/<id>/lines.json` 搬进统一文件 ✗（一次就够 ✅）。"""

    old = _legacy_lines_file(character_id)
    if not old.is_file():
        return None
    try:
        data = json.loads(old.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    lines = [str(x).strip() for x in (data.get("lines") or []) if str(x).strip()]
    if not lines:
        return None
    entry = {
        "generated_at": data.get("generated_at"),
        "model": str(data.get("model") or ""),
        "want": "",
        "fingerprint": str(data.get("fingerprint") or ""),
        "lines": lines,
    }
    return entry


def load_lines(character_id: Optional[str]) -> Dict[str, Any]:
    """读某个角色的台词池 ✗（没有 → 空池 ✗，不算错误 ✗）。"""

    cid = str(character_id or "").strip() or "default"
    empty: Dict[str, Any] = {
        "lines": [],
        "count": 0,
        "generated_at": None,
        "model": "",
        "want": "",
        "stale": False,
        "character": cid,
        "file": str(lines_file()),
    }

    chars = _read_pool_file()
    entry = chars.get(cid)

    # 统一文件里还没有 → 看看老位置有没有（一次性搬过来 ✗）
    if not isinstance(entry, dict):
        migrated = _migrate_legacy(character_id)
        if migrated is None:
            return empty
        chars[cid] = migrated
        persisted = False
        try:
            _write_pool_file(chars)
            persisted = True
        except Exception:  # noqa: BLE001
            pass
        # 搬成功才删老的 ✗（写失败就留着，下次还能再搬一次）
        if persisted:
            _drop_legacy_lines(character_id)
        entry = migrated
    elif _legacy_lines_file(character_id).is_file():
        # 统一文件里**已经有**它了 ✗ → 老位置那份就是**过时的残留** ✗
        # （迁移当时漏删、或者迁移发生在"只搬不删"那版代码里 ✗）。
        # ⚠️ 只在**统一文件里确实有条目**时才进来这里 ✗（上面 if/elif 保证了 ✗）
        #    —— 绝不会把唯一一份台词删掉 ✗。
        _drop_legacy_lines(character_id)

    lines = [str(x).strip() for x in (entry.get("lines") or []) if str(x).strip()]
    fp = str(entry.get("fingerprint") or "")
    return {
        "lines": lines,
        "count": len(lines),
        "generated_at": entry.get("generated_at"),
        "model": str(entry.get("model") or ""),
        "want": str(entry.get("want") or ""),
        # 指纹对不上 = 人设/私人信息改过了 ✗ → 池子还可用，但建议重生成
        "stale": bool(fp) and fp != _fingerprint(character_id),
        "character": cid,
        "file": str(lines_file()),
    }


def merge_lines(
    character_id: Optional[str], new_lines: List[str], model: str = "", want: str = ""
) -> Dict[str, Any]:
    """**追加**一批台词（旧的保留 ✗，重复的去掉 ✗）。

    用户要求：「每次新生成时，不要自动把之前的删掉」✗ ——
    以前是**整份覆盖** ✗，生成一次就把你手改过的、删过的全冲掉了 ✗。
    """

    old_lines = load_lines(character_id)["lines"]
    seen = set(old_lines)
    merged = list(old_lines)
    added = 0
    for x in new_lines:
        t = str(x).strip()[:MAX_LINE_CHARS]
        if t and t not in seen:
            seen.add(t)
            merged.append(t)
            added += 1
    out = save_lines(character_id, merged, model, want)
    out["added"] = added
    out["replaced"] = 0
    return out


def save_lines(
    character_id: Optional[str],
    lines: List[str],
    model: str = "",
    want: str = "",
) -> Dict[str, Any]:
    """写入某个角色的台词池 ✗（合并进统一的 `lines.yaml` ✗）。"""

    import time

    cid = str(character_id or "").strip() or "default"
    chars = _read_pool_file()
    prev = chars.get(cid) if isinstance(chars.get(cid), dict) else {}

    # ⚠️ 界面上「改字 / 删句」走的是 PUT ✗ —— 它不知道该填什么模型 / 需求 ✗。
    #    不保留的话，一次编辑就把它们抹成空 ✗（界面上显示成 `?` ✗）。
    if not model:
        model = str(prev.get("model") or "")
    if not want:
        want = str(prev.get("want") or "")

    chars[cid] = {
        "generated_at": time.time(),
        "model": model,
        "want": want,
        "fingerprint": _fingerprint(character_id),
        "lines": [str(x).strip()[:MAX_LINE_CHARS] for x in lines if str(x).strip()],
    }
    try:
        _write_pool_file(chars)
    except Exception:  # noqa: BLE001 - 写不进去也要把内存里的结果给前端 ✅
        got = load_lines(character_id)
        if not got["lines"] and chars[cid]["lines"]:
            got["lines"] = chars[cid]["lines"]
            got["count"] = len(chars[cid]["lines"])
        return got
    return load_lines(character_id)


def clear_lines(character_id: Optional[str]) -> Dict[str, Any]:
    """清空某个角色的台词池 ✗（统一文件里只删它那一节 ✗）。"""

    cid = str(character_id or "").strip() or "default"
    chars = _read_pool_file()
    if cid in chars:
        del chars[cid]
        try:
            _write_pool_file(chars)
        except Exception:  # noqa: BLE001
            pass
    # 老位置也顺手删掉（迁移过一次就别留着 ✗）
    _drop_legacy_lines(character_id)
    _BAGS.pop(cid, None)
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
        # ⚠️ 先脱 markdown 强调 ✗ —— 反过来的话，`**重点**` 会被下面的
        #    "去编号"规则当成 `*` 开头的列表项 ✗，吃掉一个星号 ✗
        #    （2026-10-14 被单测抓出来 ✗）。
        s = s.replace("**", "").strip()
        # 去编号：`1. ` / `1、` / `1)` / `- ` / `* ` / `• `
        s = re.sub(r"^\s*(?:[-*•·]|\(?\d{1,2}[.、)）:：])\s*", "", s).strip()
        # 去成对包裹的引号 / 书名号 / 括号（只去一层 ✗）
        s = re.sub(r'^["\'「『“”‘’\(（\[【]+', "", s)
        s = re.sub(r'["\'」』“”‘’\)）\]】]+$', "", s).strip()
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
    character_id: Optional[str],
    count: int = DEFAULT_BATCH,
    want: str = "",
    append: bool = False,
) -> Dict[str, Any]:
    """**一次生成一整批**台词并存档 ✗。失败抛异常 ✗。

    一次调用出几十条 ✗ —— 比"点 30 次各生成一句"省 29 次往返 ✗，
    而且**模型能看到全局**✗，配比（人设 / 她的近况 / 气氛）更像人 ✗。

    `want` = 用户写的那句**一句话需求** ✗
    （例："这次想要多一点跟找工作有关的" ✗）—— 作为**最高优先**塞进提示词 ✅。
    """

    cfg = load_config()
    if not is_enabled():
        raise RuntimeError("没配 LLM（chat.yaml 里缺 base_url / api_key / model）")

    try:
        n = int(count)
    except (TypeError, ValueError):
        n = DEFAULT_BATCH
    n = max(5, min(n, MAX_BATCH))
    want = str(want or "").strip()[:500]

    system = _BATCH_TEMPLATE.format(
        persona=load_persona(character_id),
        profile=load_profile() or "（没有额外信息 ✗）",
        want=want or _NO_WANT,
        count=n,
    )
    raw = _post_chat(
        system,
        "直接按格式输出那 {} 行 ✗。".format(n),
        # 思考型模型的 reasoning 也吃 max_tokens ✗ → 给宽一点 ✅
        max(1500, n * 120),
    )
    lines = _clean_batch(raw, n)
    if len(lines) < 3:
        raise RuntimeError("模型没给出足够多的有效台词（只拿到 {} 条 ✗）".format(len(lines)))
    model = str(cfg["model"])
    if append:
        # 追加模式：旧的留着，新的去重后接在后面
        out = merge_lines(character_id, lines, model, want)
        out["generated"] = len(lines)
        return out
    return save_lines(character_id, lines, model, want)


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
    # ⚠️ 台词池文件**坏了**要和"还没生成过"分得开 ✗（2026-10-17 踩过 ✗）：
    #    那次文件被写坏 ✗ 界面上就是"池子凭空消失"✗ 让人以为是程序吃掉了 ✗。
    _read_pool_file()  # 顺便刷新一下错误状态 ✗
    if _LAST_POOL_ERROR:
        data["broken"] = _LAST_POOL_ERROR
        data["broken_file"] = str(lines_file())
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
