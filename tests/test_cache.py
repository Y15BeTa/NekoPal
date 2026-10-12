"""缓存键与音频缓存（设计文档 5.7）。"""

import nekopal.core.models as models
from nekopal.core.cache import AudioCache
from nekopal.core.models import SynthOptions, SynthResult, cache_key


def test_key_includes_cache_version(monkeypatch):
    """缓存键必须随结果格式版本变化，否则修正过的合成逻辑会被老缓存掩盖。"""

    base = cache_key("你好", "say:X", SynthOptions())
    monkeypatch.setattr(models, "CACHE_VERSION", "999")
    assert cache_key("你好", "say:X", SynthOptions()) != base


def test_key_is_stable_for_same_inputs():
    assert cache_key("你好", "say:X", SynthOptions()) == cache_key("你好", "say:X", SynthOptions())


def test_key_changes_with_text_and_voice():
    base = cache_key("你好", "say:X", SynthOptions())
    assert cache_key("你好啊", "say:X", SynthOptions()) != base
    assert cache_key("你好", "say:Y", SynthOptions()) != base


def test_key_changes_with_synth_params():
    base = cache_key("你好", "say:X", SynthOptions())
    assert cache_key("你好", "say:X", SynthOptions(rate=1.5)) != base
    assert cache_key("你好", "say:X", SynthOptions(volume=0.5)) != base


def test_key_ignores_playback_only_params():
    """playbackRate / start_index 等播放侧参数根本不进 SynthOptions。"""

    explicit = SynthOptions(rate=1.0, pitch=0.0, volume=1.0)
    assert cache_key("你好", "say:X", SynthOptions()) == cache_key("你好", "say:X", explicit)


def test_cache_roundtrip(tmp_path):
    cache = AudioCache(tmp_path, max_mb=10)
    options = SynthOptions()
    key = cache.key("你好", "say:X", options)

    assert cache.load(key) is None

    result = SynthResult(
        audio=b"RIFF" + b"\x00" * 60,
        sample_rate=22050,
        duration=1.25,
        words=[],
        media_type="audio/wav",
        extension="wav",
    )
    stored = cache.store(key, "你好", "say:X", result)
    assert stored.duration == 1.25

    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.audio_id == key
    assert loaded.media_type == "audio/wav"
    assert loaded.path.is_file()

    event = loaded.to_event(3)
    assert event["i"] == 3
    assert event["audio"] == "/api/audio/{}".format(key)


def test_cache_evict_removes_oldest(tmp_path):
    cache = AudioCache(tmp_path, max_mb=0)  # 上限 0 → 全部淘汰
    result = SynthResult(audio=b"x" * 1024, sample_rate=22050, duration=0.1)
    key = cache.key("甲", "say:X", SynthOptions())
    cache.store(key, "甲", "say:X", result)

    assert cache.evict() >= 1
    assert cache.load(key) is None
