"""配置加载。

MVP 阶段只支持「代码默认值 + 环境变量」两层；
设计文档 6.8 描述的 YAML 文件分层留待 Phase 2 实现。
"""

from __future__ import annotations

import os
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
    default_voice: str = "edge:zh-CN-XiaoxiaoNeural"
    # 离线 / 合成失败时自动回退到的音色（同样必须是 official 等级）
    fallback_voice: str = "say:auto"
    default_backend: str = "say"

    cache_dir: Path = field(default_factory=default_cache_dir)
    cache_max_mb: int = 500

    clean: List[str] = field(default_factory=lambda: list(DEFAULT_CLEAN))
    rate: float = 1.0
    auto_scroll: str = "center"

    # 预合成队列的前瞻段数（设计文档 5.4）
    prefetch: int = 3

    web_dir: Optional[Path] = field(default_factory=default_web_dir)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "default_voice": self.default_voice,
            "fallback_voice": self.fallback_voice,
            "default_backend": self.default_backend,
            "cache_dir": str(self.cache_dir),
            "cache_max_mb": self.cache_max_mb,
            "clean": list(self.clean),
            "rate": self.rate,
            "auto_scroll": self.auto_scroll,
            "prefetch": self.prefetch,
        }


def load_settings() -> Settings:
    s = Settings()
    s.host = _env("NEKOPAL_HOST", s.host)
    s.port = _env_int("NEKOPAL_PORT", s.port)
    s.default_voice = _env("NEKOPAL_DEFAULT_VOICE", s.default_voice)
    s.fallback_voice = _env("NEKOPAL_FALLBACK_VOICE", s.fallback_voice)
    s.default_backend = _env("NEKOPAL_DEFAULT_BACKEND", s.default_backend)
    s.cache_dir = Path(_env("NEKOPAL_CACHE_DIR", str(s.cache_dir)))
    s.cache_max_mb = _env_int("NEKOPAL_CACHE_MAX_MB", s.cache_max_mb)
    s.rate = _env_float("NEKOPAL_RATE", s.rate)
    s.prefetch = max(1, _env_int("NEKOPAL_PREFETCH", s.prefetch))

    web = os.environ.get("NEKOPAL_WEB_DIR")
    if web:
        s.web_dir = Path(web)
    return s
