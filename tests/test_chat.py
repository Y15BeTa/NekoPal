"""角色聊天 / 预生成台词池（`nekopal.chat`）。

为什么这份测试现在才补：`chat.py` 是最近改得最多的模块，却是**唯一零测试**的
—— 2026-10-14 一晚就踩到两个只有测试能拦住的坑：

1. `chat.yaml` 被写坏 → `load_config()` 静默吞掉异常 → `is_enabled()` 变 False
   → 台词悄悄退回静态那 6 句，界面上**一点提示都没有**；
2. 台词池从 `characters/<id>/lines.json` 搬进统一的 `lines.yaml` 时，
   多处调用漏了「保存时保留 model / want」，一次界面编辑就把它们抹成空。

所以这里盯着的是**失败模式**，不是覆盖率数字。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nekopal import chat


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """把 `config_dir()` 指到 tmp（`packs.config_dir` 认 `XDG_CONFIG_HOME`）。"""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    chat._BAGS.clear()
    yield tmp_path


def write_cfg(cfg, **fields):
    """写一份 `chat.yaml`。"""

    body = "\n".join("{}: {}".format(k, v) for k, v in fields.items())
    path = cfg / "nekopal"
    path.mkdir(parents=True, exist_ok=True)
    (path / "chat.yaml").write_text(body + "\n", encoding="utf-8")


# ── ① `_clean_batch`：模型不守格式，解析必须替它守 ───────────────────────


def test_clean_batch_strips_numbering_and_quotes():
    raw = '1. 「第一句」\n2) "第二句"\n- 第三句\n• 第四句'
    assert chat._clean_batch(raw, 10) == ["第一句", "第二句", "第三句", "第四句"]


def test_clean_batch_removes_duplicates_keeping_order():
    raw = "同样一句。\n别的。\n同样一句。"
    assert chat._clean_batch(raw, 10) == ["同样一句。", "别的。"]


def test_clean_batch_skips_empty_and_overlong_lines():
    raw = "短的。\n\n   \n{}\n也行。".format("长" * 60)
    assert chat._clean_batch(raw, 10) == ["短的。", "也行。"]


def test_clean_batch_truncates_to_max_line_chars():
    # 40 字以内才收（更长的整行丢掉）；收进来的按 MAX_LINE_CHARS 截断
    raw = "字" * 35
    out = chat._clean_batch(raw, 10)
    assert len(out) == 1 and len(out[0]) == chat.MAX_LINE_CHARS


def test_clean_batch_respects_wanted_count():
    raw = "\n".join("第{}句。".format(i) for i in range(20))
    assert len(chat._clean_batch(raw, 5)) == 5


def test_clean_batch_strips_markdown_emphasis():
    assert chat._clean_batch("**重点**", 5) == ["重点"]


# ── ② `_strip_line`：单句输出也得收拾 ──────────────────────────────────


def test_strip_line_keeps_only_first_line():
    # 只取第一行；**句号保留**（切句不该吃掉它）
    assert chat._strip_line("第一句。\n（这里本来要解释一下）") == "第一句。"


def test_strip_line_keeps_single_trailing_period():
    """⚠️ 老实现一律在第一个「。」处切掉 → 现编的台词**永远不带句号**，
    而预生成池里的都带 —— 两条路输出不一致。"""

    assert chat._strip_line("嗯。") == "嗯。"


def test_strip_line_splits_extra_sentences_but_keeps_punctuation():
    assert chat._strip_line("甲。乙。") == "甲。"


def test_strip_line_unwraps_paired_quotes():
    assert chat._strip_line("「在的。」") == "在的。"
    assert chat._strip_line('"在的。"') == "在的。"


def test_strip_line_caps_length():
    assert len(chat._strip_line("字" * 100)) == chat.MAX_LINE_CHARS


# ── ③ 配置：坏掉必须**能被看见**（这是那个坑的正题）─────────────────────


def test_missing_config_is_not_an_error(cfg):
    assert chat.is_enabled() is False
    assert chat.config_error() is None  # 没配 = 正常状态，不是错误


def test_enabled_requires_all_three_fields(cfg):
    write_cfg(cfg, base_url="https://x", api_key="k", model="m")
    assert chat.is_enabled() is True
    write_cfg(cfg, base_url="https://x", api_key="k", model="")
    assert chat.is_enabled() is False


def test_broken_yaml_is_reported_not_swallowed(cfg):
    """⚠️ 这条就是那个坑：坏了绝不能静默失效。"""

    path = cfg / "nekopal"
    path.mkdir(parents=True, exist_ok=True)
    (path / "chat.yaml").write_text(
        "base_url: https://x\napi_key: k\nmodel: m\ntimeout: 4\n5\n", encoding="utf-8"
    )
    assert chat.is_enabled() is False
    err = chat.config_error()
    assert err and "Error" in err  # yaml 的 ScannerError 之类


def test_public_status_never_leaks_the_key(cfg):
    write_cfg(cfg, base_url="https://x", api_key="sk-super-secret", model="m")
    status = chat.public_status()
    assert status["has_key"] is True
    assert "sk-super-secret" not in json.dumps(status, ensure_ascii=False)
    assert status["config_error"] is None


# ── ④ 台词池：存 / 取 / 合 / 清 ────────────────────────────────────────


def test_empty_pool_is_not_an_error(cfg):
    got = chat.load_lines("rana")
    assert got["count"] == 0 and got["lines"] == []
    assert Path(got["file"]).name == "lines.yaml"


def test_pool_roundtrip(cfg):
    chat.save_lines("rana", ["第一句。", "第二句。"], "m", "想多点找工作的")
    got = chat.load_lines("rana")
    assert got["lines"] == ["第一句。", "第二句。"]
    assert got["model"] == "m"
    assert got["want"] == "想多点找工作的"


def test_save_without_model_keeps_previous(cfg):
    """界面上的「改字 / 删句」走 PUT，带不了 model —— 不能因此把它抹空。"""

    chat.save_lines("rana", ["甲。", "乙。"], "deepseek-flash", "要短")
    chat.save_lines("rana", ["甲。", "乙。", "丙。"])  # 模拟界面编辑
    got = chat.load_lines("rana")
    assert got["model"] == "deepseek-flash"
    assert got["want"] == "要短"
    assert got["count"] == 3


def test_pools_of_different_characters_do_not_mix(cfg):
    chat.save_lines("rana", ["她说的。"], "m")
    chat.save_lines("anon", ["别人说的。"], "m")
    assert chat.load_lines("rana")["lines"] == ["她说的。"]
    assert chat.load_lines("anon")["lines"] == ["别人说的。"]


def test_clear_only_removes_that_character(cfg):
    chat.save_lines("rana", ["甲。"], "m")
    chat.save_lines("anon", ["乙。"], "m")
    chat.clear_lines("rana")
    assert chat.load_lines("rana")["count"] == 0
    assert chat.load_lines("anon")["count"] == 1


def test_pool_file_is_private(cfg):
    chat.save_lines("rana", ["甲。"], "m")
    mode = (cfg / "nekopal" / "lines.yaml").stat().st_mode & 0o777
    assert mode == 0o600  # 私人语境衍生物，只给本人读写


def test_legacy_json_pool_is_migrated(cfg):
    """老位置 `characters/<id>/lines.json` 要能自动搬进统一的 `lines.yaml`。"""

    old = cfg / "nekopal" / "characters" / "rana"
    old.mkdir(parents=True, exist_ok=True)
    (old / "lines.json").write_text(
        json.dumps({"lines": ["老句子。"], "model": "old", "generated_at": 1.0}),
        encoding="utf-8",
    )
    got = chat.load_lines("rana")
    assert got["lines"] == ["老句子。"]
    assert got["model"] == "old"
    # 搬完就该写进统一文件
    assert (cfg / "nekopal" / "lines.yaml").is_file()


def test_stale_flag_tracks_persona_or_profile_change(cfg):
    chat.save_lines("rana", ["甲。"], "m")
    assert chat.load_lines("rana")["stale"] is False

    profile = cfg / "nekopal" / "profile.md"
    profile.write_text("换了一份个人信息。", encoding="utf-8")
    assert chat.load_lines("rana")["stale"] is True


# ── ⑤ 抽签袋：一轮之内不重复 ───────────────────────────────────────────


def test_pick_line_cycles_without_repeating(cfg):
    lines = ["甲。", "乙。", "丙。"]
    picks = [chat.pick_line("rana", lines) for _ in range(3)]
    assert sorted(picks) == sorted(lines)  # 三句各出现一次


def test_pick_line_refills_after_exhaustion(cfg):
    lines = ["甲。", "乙。"]
    [chat.pick_line("rana", lines) for _ in range(2)]
    assert chat.pick_line("rana", lines) in lines  # 抽空了自动重填


def test_pick_line_returns_none_on_empty_pool(cfg):
    assert chat.pick_line("rana", []) is None
    assert chat.pick_line("rana") is None


def test_pick_line_drops_removed_lines_from_the_bag(cfg):
    chat.pick_line("rana", ["甲。", "乙。", "丙。"])
    # 池子被改小 —— 袋子里已经删掉的不能"复活"
    for _ in range(6):
        assert chat.pick_line("rana", ["甲。"]) == "甲。"


# ── ⑥ 提示词：不许把「偶尔瞟一眼」写回「每句都叹气」────────────────────


def test_batch_prompt_caps_the_ellipsis_and_bans_sighs():
    """人设里写着「经常只回一个「……」」，模型就会每句都叹气 —— 提示词得压住。"""

    prompt = chat._BATCH_TEMPLATE
    assert "最多 4 句" in prompt.replace("不要超过 4 句", "最多 4 句")
    assert "叹词" in prompt
    assert "{want}" in prompt  # 用户的一句话需求要真的进提示词
