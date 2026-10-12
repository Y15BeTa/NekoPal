"""桌面外壳的启动 / 停止约定（**不真的起进程**，只验证约定与匹配模式）。"""

from nekopal.desktop import STOP_PATTERNS, backend_alive, default_rvc_python


def test_stop_patterns_cover_both_entry_points():
    """`nekopal down` 必须能停掉 `nekopal up` 开的窗口。

    踩过：`up` 是在**同一进程内**调用 `run_app` 开窗的，它的命令行里没有
    "nekopal app" 子串 —— 只匹配 "nekopal app" 就会漏掉它，
    于是「双击关闭猫伴」只关了后端与变声服务，**窗口还开着**（README 却承诺会关）。
    """

    patterns = [pattern for pattern, _ in STOP_PATTERNS]

    assert "nekopal app" in patterns  # 只开窗口的入口
    assert "nekopal up" in patterns  # 一键启动（同一进程内开窗）
    assert any("nekopal serve" in p for p in patterns)
    assert any("rvc/serve.py" in p for p in patterns)


def test_stop_patterns_have_labels():
    """每条都要有可读说明，`nekopal down` 的输出才不会出现空标签。"""

    for pattern, label in STOP_PATTERNS:
        assert pattern and label


def test_backend_alive_is_false_when_nothing_is_listening():
    """对一个几乎不可能有服务的端口探测，应当安静地返回 False（不抛异常）。"""

    assert backend_alive("http://127.0.0.1:1", timeout=0.2) is False


def test_default_rvc_python_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NEKOPAL_RVC_PYTHON", str(tmp_path / "python"))
    assert default_rvc_python() == tmp_path / "python"
