"""配置加载。

MVP 阶段只支持「代码默认值 + 环境变量」两层；
设计文档 6.8 描述的 YAML 文件分层留待 Phase 2 实现。
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# 设计文档 5.1：清洗链的默认顺序与开关
DEFAULT_CLEAN = [
    "normalize",
    "skip_code",
    "skip_tables",
    "strip_markdown",
    "speak_symbols",
    "strip_emoji",
]


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "nekopal"


def default_web_dir() -> Optional[Path]:
    """仓库内的 ``web/`` 目录。"""
    # src/nekopal/config.py -> src/nekopal -> src -> <repo>
    candidate = Path(__file__).resolve().parents[2] / "web"
    return candidate if candidate.is_dir() else None


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    """从环境变量读布尔（认 1/true/yes/on ✗，大小写不敏感 ✗）。"""

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8787

    # 默认音色：只允许 official 等级（设计文档 4.7 / G10）
    # Edge-TTS 的「晓晓」体验最好；它是联网音色，因此必须有离线兜底。
    #: 外部推送（opencode 插件等）进来时，**要不要自动开始朗读** ✗
    #: 关掉它 = 只接收、不自动出声（你手动点「朗读」仍然正常 ✗）
    auto_read: bool = True
    default_voice: str = "edge:zh-CN-XiaoxiaoNeural"
    # 离线 / 合成失败时自动回退到的音色（同样必须是 official 等级）
    fallback_voice: str = "say:auto"
    default_backend: str = "say"

    cache_dir: Path = field(default_factory=default_cache_dir)
    cache_max_mb: int = 500

    clean: List[str] = field(default_factory=lambda: list(DEFAULT_CLEAN))
    rate: float = 1.0
    #: 变调（半音 ✗）。**RVC 后端用** —— 走 `options.pitch` 进缓存键 ✗，
    #: 所以改了它旧缓存会自然失效 ✅
    pitch: float = 0.0
    auto_scroll: str = "center"

    # 预合成队列的前瞻段数（设计文档 5.4）
    prefetch: int = 3

    #: 「边写边读」（Follow，设计文档 6.11）：客户端流式推送全文时，边收边念。
    #: 关掉它就退化成老行为 —— 整篇生成完再念。
    follow: bool = True

    #: 同一内容源在这么短时间内重复推送同一段文本，视为重复触发（插件被加载多实例）
    dedupe_window: float = 10.0

    #: 导出（通勤播客）：目录、段间静音、默认格式
    export_dir: Path = field(default_factory=lambda: Path.home() / "NekoPal" / "export")
    export_gap: float = 0.15
    export_format: str = "m4a"

    #: 变声命令模板（RVC / DDSP-SVC 等"音频 → 音频"工具）。
    #: 占位符：{input} {output} {model} {index} {pitch}
    rvc_command: List[str] = field(default_factory=list)
    rvc_timeout: float = 600.0

    web_dir: Optional[Path] = field(default_factory=default_web_dir)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "auto_read": self.auto_read,
            "default_voice": self.default_voice,
            "fallback_voice": self.fallback_voice,
            "default_backend": self.default_backend,
            "cache_dir": str(self.cache_dir),
            "cache_max_mb": self.cache_max_mb,
            "clean": list(self.clean),
            "rate": self.rate,
            "pitch": self.pitch,
            "auto_scroll": self.auto_scroll,
            "prefetch": self.prefetch,
            "follow": self.follow,
            "dedupe_window": self.dedupe_window,
            "export_dir": str(self.export_dir),
            "export_gap": self.export_gap,
            "export_format": self.export_format,
            "rvc_command": " ".join(self.rvc_command),
            "rvc_timeout": self.rvc_timeout,
        }


def load_settings() -> Settings:
    s = Settings()
    s.host = _env("NEKOPAL_HOST", s.host)
    s.port = _env_int("NEKOPAL_PORT", s.port)
    s.auto_read = _env_bool("NEKOPAL_AUTO_READ", s.auto_read)
    s.default_voice = _env("NEKOPAL_DEFAULT_VOICE", s.default_voice)
    s.fallback_voice = _env("NEKOPAL_FALLBACK_VOICE", s.fallback_voice)
    s.default_backend = _env("NEKOPAL_DEFAULT_BACKEND", s.default_backend)
    s.cache_dir = Path(_env("NEKOPAL_CACHE_DIR", str(s.cache_dir)))
    s.cache_max_mb = _env_int("NEKOPAL_CACHE_MAX_MB", s.cache_max_mb)
    s.rate = _env_float("NEKOPAL_RATE", s.rate)
    s.pitch = _env_float("NEKOPAL_PITCH", s.pitch)
    s.prefetch = max(1, _env_int("NEKOPAL_PREFETCH", s.prefetch))
    s.dedupe_window = _env_float("NEKOPAL_DEDUPE_WINDOW", s.dedupe_window)
    s.follow = _env("NEKOPAL_FOLLOW", "1" if s.follow else "0").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    s.export_dir = Path(_env("NEKOPAL_EXPORT_DIR", str(s.export_dir)))
    s.export_gap = _env_float("NEKOPAL_EXPORT_GAP", s.export_gap)
    s.export_format = _env("NEKOPAL_EXPORT_FORMAT", s.export_format)

    raw_rvc = os.environ.get("NEKOPAL_RVC_COMMAND", "").strip()
    if raw_rvc:
        s.rvc_command = shlex.split(raw_rvc)
    s.rvc_timeout = _env_float("NEKOPAL_RVC_TIMEOUT", s.rvc_timeout)

    web = os.environ.get("NEKOPAL_WEB_DIR")
    if web:
        s.web_dir = Path(web)
    return s
