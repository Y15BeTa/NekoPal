"""首次运行的工作区准备（`nekopal.bootstrap`）。

起因：2026-10-14 实测 —— 在**全新环境**里跑一遍后端，
`~/.config/nekopal/` **连文件夹都不会被创建**，用户没有任何地方放自己的东西，
也没有提示。用户问过「把项目 pull 到另一台电脑，能自动在合适的位置新建个人配置文件夹吗？」
—— 原来的答案是"不会"，这个模块把它变成"会"。
"""

from __future__ import annotations

import pytest

from nekopal import bootstrap


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "nekopal"


def test_creates_dir_and_templates(fresh):
    out = bootstrap.ensure_user_workspace()
    assert fresh.is_dir()
    for name in ("chat.yaml", "profile.md", "README.txt", "idle_prompt.md", "ui.json"):
        assert (fresh / name).is_file(), name
    assert sorted(out["created"]) == [
        "README.txt",
        "chat.yaml",
        "idle_prompt.md",
        "profile.md",
        "ui.json",
    ]


def test_never_overwrites(fresh):
    """⚠️ 铁律：只创建、绝不覆盖 —— 用户改过的文件一个字节都不能动。"""

    fresh.mkdir(parents=True, exist_ok=True)
    (fresh / "chat.yaml").write_text("base_url: 我的\n", encoding="utf-8")
    (fresh / "profile.md").write_text("我叫猫猫姐姐\n", encoding="utf-8")

    out = bootstrap.ensure_user_workspace()

    assert (fresh / "chat.yaml").read_text(encoding="utf-8") == "base_url: 我的\n"
    assert (fresh / "profile.md").read_text(encoding="utf-8") == "我叫猫猫姐姐\n"
    assert "chat.yaml" not in out["created"]
    assert "profile.md" not in out["created"]


def test_chat_template_has_no_key(fresh):
    """模板里绝不能带任何密钥。"""

    bootstrap.ensure_user_workspace()
    text = (fresh / "chat.yaml").read_text(encoding="utf-8")
    assert 'api_key: ""' in text
    assert "sk-" not in text


def test_chat_template_is_private(fresh):
    bootstrap.ensure_user_workspace()
    assert (fresh / "chat.yaml").stat().st_mode & 0o777 == 0o600


def test_reports_what_the_user_must_supply(fresh):
    """模型素材程序**不代为分发**（有版权）—— 但要明确告诉用户缺什么。"""

    out = bootstrap.ensure_user_workspace()
    assert set(out["missing"]) == {"core", "voices", "characters"}


def test_idempotent(fresh):
    bootstrap.ensure_user_workspace()
    again = bootstrap.ensure_user_workspace()
    assert again["created"] == []


# ── R26：音色从"全局一个值"改成"每个角色一个值"时的一次性迁移 ──────────────
#
# **为什么必须做**：改完之后，角色包里那句 `voice: rana` 会**第一次真的生效**
# （以前它一直被全局值盖着 ✗）。如果角色包声明的和用户选的不一样 ✗
# —— 用户明明选过的音色就会被**悄悄改掉** ✗。
# ⇒ 升级时先把旧值记到"当前角色"名下 ✅。


def _write_ui(fresh, data):
    import json

    (fresh / "ui.json").write_text(json.dumps(data), encoding="utf-8")


def _read_ui(fresh):
    import json

    return json.loads((fresh / "ui.json").read_text(encoding="utf-8"))


def test_migrates_legacy_global_voice_onto_the_current_character(fresh):
    bootstrap.ensure_user_workspace()
    _write_ui(fresh, {"voice": "rvc:rana", "character": "rana", "pitch": 2.0})

    out = bootstrap.ensure_user_workspace()

    assert out["migrated"] == "rvc:rana → voices[rana]"
    assert _read_ui(fresh)["voices"] == {"rana": "rvc:rana"}
    # ⚠️ 别的键**一个都不许丢**（"只创建、绝不覆盖"那条红线）
    assert _read_ui(fresh)["voice"] == "rvc:rana"
    assert _read_ui(fresh)["pitch"] == 2.0


def test_migration_never_overwrites_a_choice_the_user_made_later(fresh):
    bootstrap.ensure_user_workspace()
    _write_ui(
        fresh,
        {"voice": "rvc:rana", "character": "rana", "voices": {"rana": "edge:zh-CN-XiaoyiNeural"}},
    )

    out = bootstrap.ensure_user_workspace()

    assert out["migrated"] is None
    assert _read_ui(fresh)["voices"] == {"rana": "edge:zh-CN-XiaoyiNeural"}


def test_no_migration_without_a_current_character(fresh):
    """没有"当前角色"就不知道该记到谁名下 ✗ —— 那就什么都别做。"""

    bootstrap.ensure_user_workspace()
    _write_ui(fresh, {"voice": "rvc:rana"})

    assert bootstrap.ensure_user_workspace()["migrated"] is None
    assert "voices" not in _read_ui(fresh)
