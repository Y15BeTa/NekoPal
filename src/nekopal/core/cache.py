"""内容寻址音频缓存（设计文档 5.7）。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from nekopal.core.models import SynthResult, cache_key


@dataclass
class CachedSegment:
    """一段可直接播放的音频。"""

    audio_id: str
    duration: float
    words: List[Dict[str, Any]]
    media_type: str
    path: Path

    def to_event(self, index: int) -> Dict[str, Any]:
        return {
            "i": index,
            "audio": f"/api/audio/{self.audio_id}",
            "duration": round(self.duration, 3),
            "words": self.words,
        }


class AudioCache:
    def __init__(self, root: Path, max_mb: int = 500) -> None:
        self.root = Path(root) / "audio"
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_mb = max_mb

    # -- 键 --------------------------------------------------------------
    @staticmethod
    def key(text: str, voice_id: str, options) -> str:
        return cache_key(text, voice_id, options)

    def _meta_path(self, key: str) -> Path:
        return self.root / "{}.json".format(key)

    # -- 读 --------------------------------------------------------------
    def load(self, key: str) -> Optional[CachedSegment]:
        meta_path = self._meta_path(key)
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except (ValueError, OSError):
            return None
        audio_path = self.root / meta.get("file", "")
        if not audio_path.is_file():
            return None
        try:
            os.utime(audio_path, None)  # 触摸以维持 LRU 顺序
        except OSError:
            pass
        return CachedSegment(
            audio_id=key,
            duration=float(meta.get("duration", 0.0)),
            words=list(meta.get("words") or []),
            media_type=str(meta.get("media_type", "audio/wav")),
            path=audio_path,
        )

    # -- 写 --------------------------------------------------------------
    def store(self, key: str, text: str, voice_id: str, result: SynthResult) -> CachedSegment:
        audio_path = self.root / "{}.{}".format(key, result.extension)
        audio_path.write_bytes(result.audio)
        meta = {
            "text": text,
            "voice": voice_id,
            "duration": round(result.duration, 4),
            "sample_rate": result.sample_rate,
            "words": result.words_dict(),
            "media_type": result.media_type,
            "file": audio_path.name,
            "created_at": time.time(),
        }
        self._meta_path(key).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        return CachedSegment(
            audio_id=key,
            duration=result.duration,
            words=result.words_dict(),
            media_type=result.media_type,
            path=audio_path,
        )

    # -- 维护 ------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        total = 0
        count = 0
        for path in self.root.glob("*"):
            if path.is_file():
                total += path.stat().st_size
                count += 1
        return {"files": count, "bytes": total, "mb": round(total / 1048576, 2)}

    def evict(self) -> int:
        """LRU 淘汰：按 mtime 从旧到新删除，直到总大小低于上限。"""

        limit = self.max_mb * 1048576
        entries = [
            (p.stat().st_mtime, p)
            for p in self.root.glob("*")
            if p.is_file()
        ]
        total = sum(p.stat().st_size for _, p in entries)
        if total <= limit:
            return 0
        removed = 0
        for _, path in sorted(entries):
            if total <= limit:
                break
            try:
                total -= path.stat().st_size
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed
