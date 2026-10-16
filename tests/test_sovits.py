"""GPT-SoVITS 后端（设计文档 6.1 的可插拔后端 + R24）。

这里刻意**起一个真的 HTTP 服务**（不是把网络层 mock 掉）——
因为最容易出错的恰恰是"请求体长什么样""服务挂了会怎样"这类**协议层**的事，
mock 掉就全都测不到了。
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from nekopal.core.models import SynthOptions
from nekopal.packs import VoicePack
from nekopal.tts.sovits import SovitsBackend, SovitsError

# --------------------------------------------------------------------------
# 假服务
# --------------------------------------------------------------------------

def wav_bytes(seconds: float = 0.2, rate: int = 32000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class FakeService:
    """一个最小可用的"GPT-SoVITS 服务"：记下请求，回一段 WAV。"""

    def __init__(self, *, fail_status: int | None = None, body: bytes | None = None):
        self.requests: list[tuple[str, dict]] = []
        self.fail_status = fail_status
        self.body = body if body is not None else wav_bytes()
        self.thread: threading.Thread | None = None
        self.server: ThreadingHTTPServer | None = None
        self.url = ""

    def record(self, path: str, payload: dict) -> None:
        self.requests.append((path, payload))

    def reply(self) -> tuple[int, bytes, str]:
        if self.fail_status:
            return self.fail_status, b'{"detail":"boom"}', "application/json"
        return 200, self.body, "audio/wav"

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> str:
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server 的命名
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:  # noqa: BLE001
                    payload = {"__unparsable__": raw[:80].decode("utf-8", "replace")}
                service.record(self.path, payload)
                status, body, ctype = service.reply()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                service.record(self.path, {})
                body = json.dumps({"status": "ok", "version": "v2ProPlus"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # noqa: D102 - 测试里别刷屏
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}".format(self.server.server_address[1])
        return self.url

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


@pytest.fixture
def service():
    svc = FakeService()
    svc.start()
    yield svc
    svc.stop()


# --------------------------------------------------------------------------
# 帮手
# --------------------------------------------------------------------------

def make_backend(endpoint: str, pack: VoicePack | None = None) -> SovitsBackend:
    backend = SovitsBackend(SimpleNamespace(sovits_endpoint=endpoint, sovits_timeout=10.0))
    backend.attach(None, lambda _voice_id: pack)
    return backend


def make_pack(tmp_path, **overrides) -> VoicePack:
    (tmp_path / "ref.wav").write_bytes(wav_bytes(0.3))
    fields = dict(
        id="ritsu",
        name="律",
        backend="sovits",
        path=str(tmp_path),
        license="unofficial",
        commercial_ok=False,
        redistributable=False,
        ref_audio="ref.wav",
        prompt_text="おはよう、いい天気ですね。",
        prompt_lang="all_ja",
    )
    fields.update(overrides)
    return VoicePack(**fields)


def synth(backend: SovitsBackend, pack: VoicePack, text="こんにちは", **options):
    return asyncio.run(backend.synth(pack.full_id, text, SynthOptions(**options)))


# --------------------------------------------------------------------------
# ① available()：**刻意不探测服务活没活**
# --------------------------------------------------------------------------

def test_available_is_true_when_configured_even_if_the_service_is_down(tmp_path):
    """配了就报可用 —— **哪怕服务根本没起**。

    这不是疏忽，是刻意的：`TTSRegistry.resolve_voice_id` 会在某后端
    `available() is False` 时把音色**悄悄换成 fallback**。如果这里去探测服务，
    用户选了「夢限大·律」而服务恰好没起，就会**无声无息地变成系统音色** ——
    听起来像生效了，其实完全是另一个声音（本项目踩过一整批这种"静默降级"）。

    所以：配了就 True，真打不通在 synth 里**大声报错**。
    """

    backend = SovitsBackend(SimpleNamespace(sovits_endpoint="http://127.0.0.1:1", sovits_timeout=1.0))
    assert backend.available() is True

    # 而真的去用，就会得到一个说得清楚的错误（不是悄悄换音色）
    backend.attach(None, lambda _v: make_pack(tmp_path))
    with pytest.raises(SovitsError) as excinfo:
        synth(backend, make_pack(tmp_path))
    assert "连不上" in str(excinfo.value)
    assert "integrations/sovits/serve.py" in str(excinfo.value)


def test_available_is_false_without_an_endpoint():
    backend = SovitsBackend(SimpleNamespace(sovits_endpoint="", sovits_timeout=1.0))
    assert backend.available() is False

    # 而且报错要说清"是没配"，不是"连不上"
    with pytest.raises(SovitsError) as excinfo:
        asyncio.run(backend.synth("sovits:any", "你好", SynthOptions()))
    assert "没有配置" in str(excinfo.value)


def test_capabilities_do_not_lie():
    """能力位是**给前端协商用的**，不能撒谎：

    · `word_timings=False` —— 服务只回音频，没有词级时间戳（前端该退回句级）
    · `pitch=False` —— 接口没有音高旋钮（想变调得换参考音频），报 True 会骗前端
    """

    caps = SovitsBackend(SimpleNamespace(sovits_endpoint="http://x")).capabilities()
    assert caps.word_timings is False
    assert caps.pitch is False
    assert caps.rate is True       # → 映射到请求里的 speed
    assert caps.offline is True    # 本地服务


# --------------------------------------------------------------------------
# ② synth：请求体契约
# --------------------------------------------------------------------------

def test_synth_sends_the_stateless_payload(service: FakeService, tmp_path):
    """一次请求就要带全所需信息（**无状态**）—— 服务端不需要记住"上次设过谁"。"""

    pack = make_pack(tmp_path)
    backend = make_backend(service.url, pack)

    result = synth(backend, pack, text="こんにちは", rate=0.85)

    assert len(service.requests) == 1
    path, payload = service.requests[0]
    assert path == "/tts"
    assert payload["text"] == "こんにちは"
    assert payload["text_lang"] == "all_ja"
    assert payload["prompt_text"] == "おはよう、いい天気ですね。"
    assert payload["prompt_lang"] == "all_ja"
    # 参考音频落成**绝对路径**（服务端在别的进程里，相对路径没意义）
    assert payload["ref_audio"] == str(tmp_path / "ref.wav")
    # 语速走 rate 这一个旋钮（和别的后端一致）
    assert payload["speed"] == 0.85

    assert result.media_type == "audio/wav"
    assert result.sample_rate == 32000
    assert result.words == []  # 没有时间戳 → 空数组，客户端要容忍


def test_synth_sends_per_character_weights_when_present(service: FakeService, tmp_path):
    """交付包那种"每角色一套权重"要原样带过去（绝对路径）。"""

    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "ritsu_GPT_e8.ckpt").write_bytes(b"x")
    (tmp_path / "w" / "ritsu_SoVITS_e8.pth").write_bytes(b"x")
    pack = make_pack(
        tmp_path,
        sovits_gpt_weights=str(tmp_path / "w" / "ritsu_GPT_e8.ckpt"),
        sovits_weights=str(tmp_path / "w" / "ritsu_SoVITS_e8.pth"),
    )
    backend = make_backend(service.url, pack)
    synth(backend, pack)

    _, payload = service.requests[0]
    assert payload["gpt_weights"].endswith("ritsu_GPT_e8.ckpt")
    assert payload["sovits_weights"].endswith("ritsu_SoVITS_e8.pth")


def test_text_lang_defaults_to_prompt_lang(service: FakeService, tmp_path):
    pack = make_pack(tmp_path)
    backend = make_backend(service.url, pack)
    synth(backend, pack)
    assert service.requests[0][1]["text_lang"] == pack.prompt_lang


def test_text_lang_can_override(service: FakeService, tmp_path):
    pack = make_pack(tmp_path, sovits_text_lang="zh")
    backend = make_backend(service.url, pack)
    synth(backend, pack)
    assert service.requests[0][1]["text_lang"] == "zh"


# --------------------------------------------------------------------------
# ③ 出错时**说清楚是哪一种**
# --------------------------------------------------------------------------

def test_service_error_status_is_reported_with_body(tmp_path):
    """服务端 4xx/5xx：把状态码和响应体一起报出来（不然只能靠猜）。"""

    bad = FakeService(fail_status=500)
    bad.start()
    try:
        pack = make_pack(tmp_path)
        backend = make_backend(bad.url, pack)
        with pytest.raises(SovitsError) as excinfo:
            synth(backend, pack)
        message = str(excinfo.value)
        assert "500" in message
        assert "boom" in message
    finally:
        bad.stop()


def test_non_wav_response_is_rejected(tmp_path):
    """服务回了非 WAV（比如一段 JSON 错误）—— 不能假装成功往播放器里塞。"""

    weird = FakeService(body=b'{"detail":"nope"}')
    weird.start()
    try:
        pack = make_pack(tmp_path)
        backend = make_backend(weird.url, pack)
        with pytest.raises(SovitsError) as excinfo:
            synth(backend, pack)
        assert "不是 WAV" in str(excinfo.value)
    finally:
        weird.stop()


def test_missing_ref_audio_and_prompt_text_are_clear(tmp_path):
    pack = make_pack(tmp_path, ref_audio="nope.wav")
    backend = SovitsBackend(SimpleNamespace(sovits_endpoint="http://127.0.0.1:1"))
    backend.attach(None, lambda _v: pack)
    with pytest.raises(SovitsError) as excinfo:
        synth(backend, pack)
    assert "参考音频不存在" in str(excinfo.value)

    pack2 = make_pack(tmp_path, prompt_text="")
    backend2 = SovitsBackend(SimpleNamespace(sovits_endpoint="http://127.0.0.1:1"))
    backend2.attach(None, lambda _v: pack2)
    with pytest.raises(SovitsError) as excinfo2:
        synth(backend2, pack2)
    assert "prompt_text" in str(excinfo2.value)


def test_unknown_voice_pack_is_a_clear_error():
    backend = SovitsBackend(SimpleNamespace(sovits_endpoint="http://127.0.0.1:1"))
    backend.attach(None, lambda _v: None)
    with pytest.raises(SovitsError) as excinfo:
        asyncio.run(backend.synth("sovits:ghost", "你好", SynthOptions()))
    assert "找不到" in str(excinfo.value)


# --------------------------------------------------------------------------
# ④ probe()：想查"服务活没活"时**显式**去查
# --------------------------------------------------------------------------

def test_probe_reports_alive():
    alive = FakeService()
    alive.start()
    try:
        backend = SovitsBackend(SimpleNamespace(sovits_endpoint=alive.url))
        probe = backend.probe()
        assert probe["alive"] is True
        assert probe["detail"]["status"] == "ok"
    finally:
        alive.stop()


def test_probe_reports_dead_with_a_hint():
    backend = SovitsBackend(SimpleNamespace(sovits_endpoint="http://127.0.0.1:1"))
    probe = backend.probe()
    assert probe["alive"] is False
    assert "serve.py" in probe["hint"]


# --------------------------------------------------------------------------
# ⑤ 注册表里认得它
# --------------------------------------------------------------------------

def test_registry_knows_the_backend():
    from nekopal.tts.registry import build_registry

    registry = build_registry(SimpleNamespace(sovits_endpoint="", sovits_timeout=1.0))
    backend = registry.get("sovits")
    assert backend is not None
    assert backend.id == "sovits"
