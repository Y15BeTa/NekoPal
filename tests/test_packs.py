"""音色包 / 角色包的扫描与校验（设计文档 4.4 / 4.5）。

全部离线：在临时目录里造包，不碰用户的真实配置目录。
"""

from pathlib import Path

import pytest
import yaml

from nekopal.packs import load_packs, write_template


def write_pack(base: Path, name: str, kind: str, data: dict) -> Path:
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    filename = "voice.yaml" if kind == "voice" else "character.yaml"
    (folder / filename).write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    return folder


def voice_data(pack_id: str = "my-voice", **overrides) -> dict:
    data = {
        "id": pack_id,
        "name": "测试音色",
        "backend": "edge",
        "license": "self-recorded",
        "commercial_ok": True,
        "redistributable": False,
        "edge": {"voice": "zh-CN-XiaoxiaoNeural"},
    }
    data.update(overrides)
    return data


def character_data(pack_id: str = "my-char", **overrides) -> dict:
    data = {
        "id": pack_id,
        "name": "测试角色",
        "voice": "my-voice",
        "license": "self-recorded",
        "commercial_ok": True,
        "redistributable": False,
    }
    data.update(overrides)
    return data


def load(voices_dir=None, characters_dir=None, backends=("say", "edge")):
    return load_packs(
        voice_dirs=[voices_dir] if voices_dir else [],
        character_dirs=[characters_dir] if characters_dir else [],
        known_backends=backends,
    )


# ---- 音色包 ---------------------------------------------------------------

def test_loads_valid_voice_pack(tmp_path):
    write_pack(tmp_path, "my-voice", "voice", voice_data())
    collection = load(voices_dir=tmp_path)

    assert len(collection.voices) == 1
    pack = collection.voices[0]
    assert pack.id == "my-voice"
    assert pack.full_id == "edge:zh-CN-XiaoxiaoNeural"  # backend:local
    assert pack.license == "self-recorded"
    assert pack.commercial_ok is True
    assert pack.redistributable is False
    assert collection.problems == []


def test_voice_pack_reports_missing_required_field(tmp_path):
    data = voice_data()
    del data["license"]
    write_pack(tmp_path, "my-voice", "voice", data)

    collection = load(voices_dir=tmp_path)
    assert collection.voices == []
    assert len(collection.problems) == 1
    assert "license" in collection.problems[0].reason


def test_voice_pack_rejects_illegal_license_level(tmp_path):
    write_pack(tmp_path, "my-voice", "voice", voice_data(license="totally-fine"))
    collection = load(voices_dir=tmp_path)
    assert collection.voices == []
    assert "license" in collection.problems[0].reason


def test_voice_pack_rejects_id_folder_mismatch(tmp_path):
    write_pack(tmp_path, "my-voice", "voice", voice_data(id="other-id"))
    collection = load(voices_dir=tmp_path)
    assert collection.voices == []
    assert "目录名" in collection.problems[0].reason


def test_voice_pack_rejects_unknown_backend(tmp_path):
    write_pack(tmp_path, "my-voice", "voice", voice_data(backend="magic"))
    collection = load(voices_dir=tmp_path)
    assert collection.voices == []
    assert "未注册" in collection.problems[0].reason


def test_sovits_requires_existing_ref_audio(tmp_path):
    folder = write_pack(
        tmp_path, "cloned", "voice",
        voice_data(id="cloned", backend="sovits", sovits={"ref_audio": "ref.wav", "prompt_text": "你好"}),
    )
    collection = load(voices_dir=tmp_path, backends=("say", "edge", "sovits"))
    assert collection.voices == []
    assert "参考音频不存在" in collection.problems[0].reason

    (folder / "ref.wav").write_bytes(b"RIFF....")
    collection = load(voices_dir=tmp_path, backends=("say", "edge", "sovits"))
    assert len(collection.voices) == 1
    assert collection.voices[0].backend == "sovits"


def test_ignored_folders_and_empty_dirs(tmp_path):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "no-yaml").mkdir()
    collection = load(voices_dir=tmp_path)
    assert collection.voices == []
    assert collection.problems == []


def test_one_bad_pack_does_not_block_others(tmp_path):
    write_pack(tmp_path, "good", "voice", voice_data(id="good"))
    write_pack(tmp_path, "bad", "voice", voice_data(id="bad", license="nonsense"))

    collection = load(voices_dir=tmp_path)
    assert [pack.id for pack in collection.voices] == ["good"]
    assert len(collection.problems) == 1


def test_user_dir_takes_precedence_over_repo_dir(tmp_path):
    user = tmp_path / "user"
    repo = tmp_path / "repo"
    write_pack(user, "same", "voice", voice_data(id="same", name="用户版"))
    write_pack(repo, "same", "voice", voice_data(id="same", name="仓库版"))

    collection = load_packs(
        voice_dirs=[user, repo], character_dirs=[], known_backends=("edge",)
    )
    assert len(collection.voices) == 1
    assert collection.voices[0].name == "用户版"


# ---- 角色包 ---------------------------------------------------------------

def test_character_without_model_still_loads(tmp_path):
    """模型缺失**不是错误**：降级为「没有形象的音色」，朗读不受影响。"""

    write_pack(tmp_path, "my-char", "character", character_data(avatar={"type": "live2d", "model": "model/x.model3.json"}))
    collection = load(characters_dir=tmp_path)

    assert len(collection.characters) == 1
    assert collection.problems == []
    pack = collection.characters[0]
    assert pack.to_dict()["avatar_ready"] is False
    assert pack.to_dict()["avatar"]["exists"] is False


def test_character_with_existing_model_is_ready(tmp_path):
    folder = write_pack(
        tmp_path, "my-char", "character",
        character_data(avatar={"type": "live2d", "model": "model/x.model3.json"}),
    )
    (folder / "model").mkdir()
    (folder / "model" / "x.model3.json").write_text("{}", encoding="utf-8")

    collection = load(characters_dir=tmp_path)
    assert collection.characters[0].model_exists is True
    assert collection.characters[0].to_dict()["avatar_ready"] is True


def test_character_keeps_lipsync_config(tmp_path):
    write_pack(
        tmp_path, "my-char", "character",
        character_data(
            avatar={
                "type": "live2d",
                "model": "m.model3.json",
                "lipsync": {"mode": "timings", "param": "ParamMouthOpenY"},
                "expressions": {"idle": "exp_00"},
            }
        ),
    )
    pack = load(characters_dir=tmp_path).characters[0]
    assert pack.lipsync_mode == "timings"
    assert pack.expressions == {"idle": "exp_00"}


def test_character_requires_voice_reference(tmp_path):
    data = character_data()
    del data["voice"]
    write_pack(tmp_path, "my-char", "character", data)
    collection = load(characters_dir=tmp_path)
    assert collection.characters == []
    assert "voice" in collection.problems[0].reason


# ---- 模板 -----------------------------------------------------------------

def test_write_template_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = write_template("voice", "demo")
    assert path.is_file()
    assert "backend:" in path.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        write_template("voice", "demo")  # 已存在则拒绝覆盖


def test_write_template_rejects_unknown_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        write_template("live2d", "demo")


def test_template_is_loadable(tmp_path, monkeypatch):
    """模板必须能通过自己的校验（否则用户体验会很差）。"""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    folder = write_template("voice", "demo").parent
    collection = load_packs(voice_dirs=[folder.parent], character_dirs=[], known_backends=("say", "edge", "sovits"))

    # 模板里 ref.wav 尚未提供，应当被明确提示（而不是静默通过）
    assert collection.voices == []
    assert "参考音频不存在" in collection.problems[0].reason
