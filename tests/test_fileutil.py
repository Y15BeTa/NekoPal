"""原子写 + "文件坏了要能看出来"（2026-10-17 那次静默数据丢失的回归测试）。

## 那天发生了什么

台词池 `lines.yaml` 里 `lines` 那一行**丢了一个冒号** ✗：

```yaml
    fingerprint: f40ff1ce5cf0
    lines            ← 冒号没了
    - 今天也ぽかぽか
```

于是 YAML 解析失败 ✗ —— 而 `load_lines()` **把异常吞掉、当成空池子** ✗
→ 界面上就表现成"**台词池凭空消失**"✗。

根因是 `_write_pool_file()` 用了 `path.write_text()` ✗（open 截断 → write ✗ 无保护 ✗）。
这里钉三件事：
  ① 写是**原子**的（要么全有要么全无 ✗ 不留半截文件 ✗）
  ② 写完之后**一定解析得回来** ✗
  ③ **文件坏了 ≠ 池子是空的** ✗ —— 要能报出来 ✗
"""

from __future__ import annotations

import pytest

from nekopal import chat
from nekopal.fileutil import write_text_atomic


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    chat._BAGS.clear()
    return tmp_path


# ── ① 原子写本身 ──────────────────────────────────────────────────────────


def test_atomic_write_replaces_content(tmp_path):
    target = tmp_path / "x.yaml"
    write_text_atomic(target, "第一版\n")
    assert target.read_text(encoding="utf-8") == "第一版\n"

    write_text_atomic(target, "第二版\n")
    assert target.read_text(encoding="utf-8") == "第二版\n"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "x.yaml"
    write_text_atomic(target, "内容\n")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "x.yaml"]
    assert leftovers == [], "临时文件没清干净：{}".format(leftovers)


def test_atomic_write_can_set_mode(tmp_path):
    target = tmp_path / "private.yaml"
    write_text_atomic(target, "secret\n", mode=0o600)
    assert (target.stat().st_mode & 0o777) == 0o600


def test_atomic_write_keeps_the_old_file_when_writing_fails(tmp_path, monkeypatch):
    """**写失败绝不能把旧文件弄坏** ✗ —— 这是"半截文件"的另一面。"""

    target = tmp_path / "x.yaml"
    write_text_atomic(target, "好的旧内容\n")

    class Boom:
        def write(self, *_args, **_kwargs):
            raise OSError("磁盘满了")

        def flush(self):
            pass

    monkeypatch.setattr("tempfile.NamedTemporaryFile", lambda **_kw: Boom())
    # Boom 不是上下文管理器 → 会直接抛，但不管抛什么，旧文件都必须还在
    with pytest.raises(Exception):
        write_text_atomic(target, "新内容\n")

    assert target.read_text(encoding="utf-8") == "好的旧内容\n"


# ── ② 台词池写完之后一定读得回来 ───────────────────────────────────────────


def test_saved_lines_always_load_back(cfg):
    """存一次、读一次 —— 内容必须一模一样（含 `want` 里的全角标点 ✗）。"""

    want = "用「猫猫姐姐」称呼开头；多聊吉他；句子短一点"
    chat.save_lines("nonoka", ["今天也ぽかぽか", "ののちゃん在练吉他哦"], "m", want=want)

    got = chat.load_lines("nonoka")
    assert got["lines"] == ["今天也ぽかぽか", "ののちゃん在练吉他哦"]
    assert got["want"] == want


def test_lines_survive_a_broken_colon_in_an_earlier_write(cfg):
    """回归：写过一次之后，文件里**不能出现**没冒号的 `lines`（那天就是这么坏的 ✗）。"""

    chat.save_lines("nonoka", ["甲", "乙"], "m")
    text = chat.lines_file().read_text(encoding="utf-8")
    bad = [line for line in text.splitlines() if line.strip() == "lines"]
    assert bad == [], "出现了没有冒号的 `lines` 行：{}".format(bad)


# ── ③ "文件坏了" ≠ "池子是空的" ───────────────────────────────────────────


def test_broken_pool_file_is_reported_not_swallowed(cfg):
    """**这是那次真正的教训** ✗ —— 坏掉的文件必须报出来 ✗ 不能装成空池子 ✗。"""

    path = cfg / "nekopal/lines.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    # 复刻那天的坏法：`lines` 少一个冒号
    path.write_text(
        "characters:\n"
        "  nonoka:\n"
        "    fingerprint: f40ff1ce5cf0\n"
        "    lines\n"
        "    - 今天也ぽかぽか\n",
        encoding="utf-8",
    )

    status = chat.lines_status("nonoka")

    assert status["count"] == 0                  # 读不出来 ✗ 这是事实 ✗
    assert status["broken"], "文件坏了但没报出来 ✗ —— 用户只会看到'池子凭空消失'✗"
    assert "lines.yaml" in status.get("broken_file", "")


def test_healthy_missing_file_is_not_reported_as_broken(cfg):
    """**没建过 ≠ 坏了** ✗ —— 新用户第一次打开时不该看到"文件坏了"的警告 ✗。"""

    status = chat.lines_status("nonoka")

    assert status["count"] == 0
    assert "broken" not in status
