"""变声（音频 → 音频）扩展点（设计文档 6.x）。

全部离线：用一个「把输入原样复制到输出」的假变声命令代替真实 RVC。
"""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from nekopal.core.convert import (
    CommandConverter,
    ConvertError,
    ConvertRequest,
    VoiceConverter,
)

#: 假变声器：把 {input} 原样复制到 {output}
COPY_COMMAND = [
    sys.executable,
    "-c",
    "import shutil,sys; shutil.copy(sys.argv[1], sys.argv[2])",
    "{input}",
    "{output}",
]


def make_request(tmp_path: Path) -> ConvertRequest:
    source = tmp_path / "in.wav"
    source.write_bytes(b"RIFFfake")
    return ConvertRequest(input_path=source, output_path=tmp_path / "out.wav")


def test_render_substitutes_placeholders(tmp_path):
    converter = CommandConverter(
        ["tool", "--in", "{input}", "--out", "{output}", "--m", "{model}", "--i", "{index}", "--p", "{pitch}"]
    )
    rendered = converter.render(
        ConvertRequest(
            input_path=Path("/tmp/a.wav"),
            output_path=Path("/tmp/b.wav"),
            model="/m/x.pth",
            index="/m/x.index",
            pitch=3.0,
        )
    )
    assert rendered == ["tool", "--in", "/tmp/a.wav", "--out", "/tmp/b.wav", "--m", "/m/x.pth", "--i", "/m/x.index", "--p", "3"]


def test_render_rejects_unknown_placeholder():
    converter = CommandConverter(["tool", "{nope}"])
    with pytest.raises(ConvertError) as excinfo:
        converter.render(ConvertRequest(Path("a"), Path("b")))
    assert "占位符" in str(excinfo.value)


def test_empty_template_is_unavailable():
    assert CommandConverter([]).available() is False
    assert CommandConverter(["tool"]).available() is True


def test_convert_succeeds_with_real_command(tmp_path):
    request = make_request(tmp_path)
    converter = CommandConverter(COPY_COMMAND, timeout=30)
    result = asyncio.run(converter.convert(request))
    assert result.is_file()
    assert result.read_bytes() == b"RIFFfake"


def test_convert_reports_missing_output(tmp_path):
    request = make_request(tmp_path)
    converter = CommandConverter([sys.executable, "-c", "pass"], timeout=30)
    with pytest.raises(ConvertError) as excinfo:
        asyncio.run(converter.convert(request))
    assert "没有产出" in str(excinfo.value)


def test_convert_reports_missing_binary(tmp_path):
    request = make_request(tmp_path)
    converter = CommandConverter(["definitely-not-a-real-binary-nekopal"], timeout=30)
    with pytest.raises(ConvertError) as excinfo:
        asyncio.run(converter.convert(request))
    assert "找不到命令" in str(excinfo.value)


def test_convert_reports_nonzero_exit(tmp_path):
    request = make_request(tmp_path)
    converter = CommandConverter(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        timeout=30,
    )
    with pytest.raises(ConvertError) as excinfo:
        asyncio.run(converter.convert(request))
    assert "退出码 3" in str(excinfo.value)
    assert "boom" in str(excinfo.value)


def test_convert_reports_missing_input(tmp_path):
    converter = CommandConverter(COPY_COMMAND, timeout=30)
    request = ConvertRequest(tmp_path / "nope.wav", tmp_path / "out.wav")
    with pytest.raises(ConvertError):
        asyncio.run(converter.convert(request))


def test_converter_satisfies_protocol(tmp_path):
    assert isinstance(CommandConverter(COPY_COMMAND), VoiceConverter)


def test_silent_availability():
    assert shutil.which(sys.executable) is not None  # 假命令用的解释器一定存在
