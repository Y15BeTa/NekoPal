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
