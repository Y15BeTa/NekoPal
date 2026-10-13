"""音色包 / 角色包的扫描、校验与加载（设计文档 4.4 / 4.5）。

**本项目只做接口，不打包任何素材**（N9/N10）。本模块负责：

1. 扫描用户目录与仓库目录下的包；
2. 校验必填字段（含授权三字段）；
3. 校验素材文件是否真的存在；
4. 坏包**跳过并逐条记录原因** —— 绝不让服务启动失败。

包放在哪
--------
优先用户目录（不随仓库走，最安全）::

    ~/.config/nekopal/voices/<id>/voice.yaml
    ~/.config/nekopal/characters/<id>/character.yaml

也支持仓库目录（素材已被 .gitignore 排除）::

    <repo>/voices/<id>/voice.yaml
    <repo>/characters/<id>/character.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from nekopal.core.models import LICENSE_LEVELS, Voice

#: 必填字段（两个规范共有）
_COMMON_REQUIRED = ("id", "name", "license", "commercial_ok", "redistributable")
_VOICE_REQUIRED = _COMMON_REQUIRED + ("backend",)
_CHARACTER_REQUIRED = _COMMON_REQUIRED + ("voice",)

VOICE_DIR_NAME = "voices"
CHARACTER_DIR_NAME = "characters"
CONFIG_DIR_NAME = "nekopal"


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class PackProblem:
    path: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass
class VoicePack:
    id: str
    name: str
    backend: str
    path: str
    license: str
    commercial_ok: bool
    redistributable: bool
    lang: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    description: str = ""
    ref_audio: Optional[str] = None
    prompt_text: Optional[str] = None
    prompt_lang: Optional[str] = None
    weights: Optional[str] = None
    backend_voice: Optional[str] = None
    #: 仅 backend == "rvc"（变声）时使用
    base_voice: Optional[str] = None
    rvc_model: Optional[str] = None
    rvc_index: Optional[str] = None
    #: base 的语速（<1 更慢 → 更"慵懒"）。可选。
    rvc_rate: Optional[float] = None
    command: Optional[List[str]] = None

    @property
    def full_id(self) -> str:
        """对外表示成 ``backend:local_id``，与内置音色一致。"""

        return "{}:{}".format(self.backend, self.backend_voice or self.id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.full_id,
            "pack_id": self.id,
            "name": self.name,
            "backend": self.backend,
            "source": "pack",
            "path": self.path,
            "lang": list(self.lang),
            "tags": list(self.tags),
            "description": self.description,
            "license": self.license,
            "commercial_ok": self.commercial_ok,
            "redistributable": self.redistributable,
        }

    def to_voice(self) -> Voice:
        return Voice(
            id=self.full_id,
            name=self.name,
            backend=self.backend,
            lang=list(self.lang),
            tags=list(self.tags) + ["pack"],
            license=self.license,
            commercial_ok=self.commercial_ok,
            redistributable=self.redistributable,
        )


@dataclass
class CharacterPack:
    id: str
    name: str
    voice: str
    path: str
    license: str
    commercial_ok: bool
    redistributable: bool
    description: str = ""
    avatar_type: str = ""
    avatar_model: Optional[str] = None
    model_exists: bool = False
    lipsync_mode: str = "volume"
    lipsync_param: str = "ParamMouthOpenY"
    expressions: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "voice": self.voice,
            "source": "pack",
            "path": self.path,
            "license": self.license,
            "commercial_ok": self.commercial_ok,
            "redistributable": self.redistributable,
            "description": self.description,
            "avatar": {
                "type": self.avatar_type,
                "model": self.avatar_model,
                "exists": self.model_exists,
                "lipsync": {"mode": self.lipsync_mode, "param": self.lipsync_param},
                "expressions": dict(self.expressions),
            },
            # 模型缺失时降级为「没有形象的音色」，朗读不受影响（设计文档 4.5）
            "avatar_ready": self.model_exists,
        }


@dataclass
class PackCollection:
    voices: List[VoicePack] = field(default_factory=list)
    characters: List[CharacterPack] = field(default_factory=list)
    problems: List[PackProblem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voices": [pack.to_dict() for pack in self.voices],
            "characters": [pack.to_dict() for pack in self.characters],
            "problems": [problem.to_dict() for problem in self.problems],
        }


# --------------------------------------------------------------------------
# 目录
# --------------------------------------------------------------------------

def repo_root() -> Path:
    # src/nekopal/packs.py -> src/nekopal -> src -> <repo>
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / CONFIG_DIR_NAME


def search_dirs() -> Tuple[List[Path], List[Path]]:
    """返回 ``(音色包目录们, 角色包目录们)``，按优先级从高到低。"""

    user = config_dir()
    root = repo_root()
    return (
        [user / VOICE_DIR_NAME, root / VOICE_DIR_NAME],
        [user / CHARACTER_DIR_NAME, root / CHARACTER_DIR_NAME],
    )


def ensure_user_dirs() -> Tuple[Path, Path]:
    """确保用户目录存在，返回 ``(voices, characters)``。"""

    voices = config_dir() / VOICE_DIR_NAME
    characters = config_dir() / CHARACTER_DIR_NAME
    voices.mkdir(parents=True, exist_ok=True)
    characters.mkdir(parents=True, exist_ok=True)
    return voices, characters


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("顶层必须是映射（key: value）")
    return data


def _check_common(data: Dict[str, Any], required: Sequence[str], path: Path, folder: str) -> List[str]:
    problems: List[str] = []
    for key in required:
        if data.get(key) in (None, ""):
            problems.append("缺少必填字段 `{}`".format(key))

    pack_id = str(data.get("id") or "")
    if pack_id and pack_id != folder:
        problems.append("`id` ({}) 与目录名 ({}) 不一致".format(pack_id, folder))

    license_level = str(data.get("license") or "")
    if license_level and license_level not in LICENSE_LEVELS:
        problems.append(
            "`license` 取值非法（{}），应为 {}".format(license_level, "/".join(LICENSE_LEVELS))
        )

    for key in ("commercial_ok", "redistributable"):
        if key in data and not isinstance(data[key], bool):
            problems.append("`{}` 必须是 true/false".format(key))

    return problems


def _resolve(base: Path, raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    candidate = Path(str(raw))
    return candidate if candidate.is_absolute() else base / candidate


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------

def load_packs(
    voice_dirs: Optional[Iterable[Path]] = None,
    character_dirs: Optional[Iterable[Path]] = None,
    known_backends: Optional[Iterable[str]] = None,
) -> PackCollection:
    """扫描并加载所有可用的包；坏包跳过并记入 ``problems``。"""

    if voice_dirs is None or character_dirs is None:
        default_voices, default_characters = search_dirs()
        voice_dirs = voice_dirs if voice_dirs is not None else default_voices
        character_dirs = character_dirs if character_dirs is not None else default_characters

    backends = set(known_backends) if known_backends is not None else None
    collection = PackCollection()
    seen_voices = set()

    for directory in voice_dirs:
        for folder in sorted(_subdirs(directory)):
            spec = folder / "voice.yaml"
            if not spec.is_file():
                continue
            try:
                pack = _load_voice(spec, folder, backends)
            except ValueError as exc:
                collection.problems.append(PackProblem(str(spec), str(exc)))
                continue
            if pack.id in seen_voices:
                continue  # 用户目录优先，仓库里的同名包忽略
            seen_voices.add(pack.id)
            collection.voices.append(pack)

    seen_characters = set()
    for directory in character_dirs:
        for folder in sorted(_subdirs(directory)):
            spec = folder / "character.yaml"
            if not spec.is_file():
                continue
            try:
                pack = _load_character(spec, folder)
            except ValueError as exc:
                collection.problems.append(PackProblem(str(spec), str(exc)))
                continue
            if pack.id in seen_characters:
                continue
            seen_characters.add(pack.id)
            collection.characters.append(pack)

    return collection


def _subdirs(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    return [item for item in directory.iterdir() if item.is_dir() and not item.name.startswith(".")]


def _load_voice(spec: Path, folder: Path, known_backends: Optional[set]) -> VoicePack:
    data = _load_yaml(spec)
    reasons = _check_common(data, _VOICE_REQUIRED, spec, folder.name)

    backend = str(data.get("backend") or "")
    if known_backends is not None and backend and backend not in known_backends:
        reasons.append(
            "后端 `{}` 未注册（可用：{}）".format(backend, "、".join(sorted(known_backends)))
        )

    section = data.get(backend) or {}
    base_voice = None
    resolved_model = None
    resolved_index = None
    rvc_rate = None

    if backend == "sovits":
        ref = _resolve(folder, section.get("ref_audio"))
        if ref is None:
            reasons.append("sovits 音色需要在 `{}` 段里给出 `ref_audio`".format(backend))
        elif not ref.is_file():
            reasons.append("参考音频不存在：{}".format(ref))

    if backend == "rvc":
        section = data.get("rvc") or {}
        base_voice = section.get("base_voice")
        if not base_voice:
            reasons.append("rvc 音色需要在 `rvc` 段里给出 `base_voice`（先用哪个音色合成）")
        if not section.get("model"):
            reasons.append("rvc 音色需要在 `rvc` 段里给出 `model`（你下载的变声模型）")
        #: 可选：base 的语速（<1 更慢）。「慵懒」主要靠它 —— 注意它改的是**源**的语速，
        #: 而 RVC 保留时间轴，所以最终输出也会跟着慢下来。
        raw_rate = section.get("rate")
        if raw_rate is not None:
            try:
                rvc_rate = float(raw_rate)
                if not (0.5 <= rvc_rate <= 2.0):
                    reasons.append("`rvc.rate` 建议在 0.5~2.0 之间（现在是 {}）".format(rvc_rate))
            except (TypeError, ValueError):
                reasons.append("`rvc.rate` 不是数字：{!r}".format(raw_rate))
        model_path = _resolve(folder, section.get("model"))
        index_path = _resolve(folder, section.get("index"))
        if model_path is not None and not model_path.is_file():
            reasons.append("变声模型不存在：{}".format(model_path))
        if index_path is not None and not index_path.is_file():
            reasons.append("index 文件不存在：{}".format(index_path))
        resolved_model = str(model_path) if model_path else None
        resolved_index = str(index_path) if index_path else None

    if reasons:
        raise ValueError("；".join(reasons))

    return VoicePack(
        id=str(data["id"]),
        name=str(data["name"]),
        backend=backend,
        path=str(folder),
        license=str(data["license"]),
        commercial_ok=bool(data["commercial_ok"]),
        redistributable=bool(data["redistributable"]),
        lang=[str(x) for x in (data.get("lang") or [])],
        tags=[str(x) for x in (data.get("tags") or [])],
        description=str(data.get("description") or ""),
        ref_audio=str(section.get("ref_audio") or "") or None,
        prompt_text=section.get("prompt_text"),
        prompt_lang=section.get("prompt_lang"),
        weights=str(section.get("weights") or "") or None,
        backend_voice=section.get("voice"),
        base_voice=base_voice,
        rvc_model=resolved_model,
        rvc_index=resolved_index,
        rvc_rate=rvc_rate,
        command=[str(part) for part in section.get("command")] if section.get("command") else None,
    )


def _load_character(spec: Path, folder: Path) -> CharacterPack:
    data = _load_yaml(spec)
    reasons = _check_common(data, _CHARACTER_REQUIRED, spec, folder.name)
    if reasons:
        raise ValueError("；".join(reasons))

    avatar = data.get("avatar") or {}
    avatar_type = str(avatar.get("type") or "")
    model_raw = avatar.get("model")
    model_path = _resolve(folder, model_raw)
    lipsync = avatar.get("lipsync") or {}

    return CharacterPack(
        id=str(data["id"]),
        name=str(data["name"]),
        voice=str(data["voice"]),
        path=str(folder),
        license=str(data["license"]),
        commercial_ok=bool(data["commercial_ok"]),
        redistributable=bool(data["redistributable"]),
        description=str(data.get("description") or ""),
        avatar_type=avatar_type,
        avatar_model=str(model_path) if model_path else None,
        # 模型缺失**不算错误**：降级为「没有形象的音色」（设计文档 4.5）
        model_exists=bool(model_path and model_path.is_file()),
        lipsync_mode=str(lipsync.get("mode") or "volume"),
        lipsync_param=str(lipsync.get("param") or "ParamMouthOpenY"),
        expressions={str(k): str(v) for k, v in (avatar.get("expressions") or {}).items()},
    )


# --------------------------------------------------------------------------
# 模板
# --------------------------------------------------------------------------

VOICE_TEMPLATE = """\
# 音色包定义 —— 素材由你自己准备，本项目不提供任何素材
id: __PACK_ID__
name: "我的音色"
backend: sovits                 # say | edge | sovits
lang: ["zh"]
tags: []
license: self-recorded         # official | self-recorded | licensed | public-domain | unofficial
commercial_ok: true            # 能否商用
redistributable: false         # 能否随作品/项目再分发
description: "由本人录音克隆"

# 下面只填所用后端需要的字段
sovits:
  ref_audio: ref.wav            # 3~10 秒参考音频，放本目录下
  prompt_text: "参考音频对应逐字文本"
  prompt_lang: zh

# edge:
#   voice: zh-CN-XiaoxiaoNeural

# 变声（RVC / DDSP-SVC 这类"音频→音频"的模型）—— 需要先配好变声命令：
# backend: rvc
# rvc:
#   base_voice: edge:zh-CN-XiaoxiaoNeural   # 先用谁合成（必填）
#   model: voice.pth                         # 你自己的变声模型（必填，放本目录）
#   index: voice.index                       # 可选
#   command: ["python", "infer.py", "{input}", "{output}", "--model", "{model}"]
"""

CHARACTER_TEMPLATE = """\
# 角色包定义 —— 素材由你自己准备，本项目不提供任何素材
id: __PACK_ID__
name: "我的角色"
voice: my-voice                # 引用音色包的 id
license: self-recorded         # official | self-recorded | licensed | public-domain | unofficial
commercial_ok: true
redistributable: false
description: ""

avatar:
  type: live2d
  model: model/xxx.model3.json  # 相对本目录；文件不存在时会降级为「无形象的音色」
  lipsync:
    mode: volume                # volume（音频振幅，通用）| timings（词级时间戳，更准）
    param: ParamMouthOpenY
  expressions: {}
# 可以是空目录（先只要声音），之后再把模型放进来
"""


def write_template(kind: str, pack_id: str) -> Path:
    """在用户目录生成一份包模板，返回写出的路径。"""

    voices_dir, characters_dir = ensure_user_dirs()
    if kind == "voice":
        target = voices_dir / pack_id / "voice.yaml"
        # 用简单替换而不是 str.format：模板里本来就有 {input} 这类占位符
        content = VOICE_TEMPLATE.replace("__PACK_ID__", pack_id)
    elif kind == "character":
        target = characters_dir / pack_id / "character.yaml"
        content = CHARACTER_TEMPLATE.replace("__PACK_ID__", pack_id)
    else:
        raise ValueError("kind 只能是 voice 或 character")

    if target.exists():
        raise ValueError("已存在：{}".format(target))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
