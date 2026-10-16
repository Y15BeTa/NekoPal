"""GPT-SoVITS 后端：**文字 → 目标音色的朗读**。

## 它和 `rvc` 的分工（这是它存在的理由）

    rvc    = 变声（音频 → 音频）。语气 / 断句 / 重音**全部来自 base 音色**，
             它只换"嗓子" —— 所以"要楽奈"听起来其实是"晓伊在念"。
    本后端 = 端到端 TTS。语气由**它自己的权重 + 你挑的那段参考音频**决定
             → 想换语气 = 换一段参考音频（"更贴角色"就是从这来的）

## 它自己不装引擎

对接一个**本地 GPT-SoVITS 服务**（本项目的 `integrations/sovits/serve.py`）。
所以 NekoPal 的依赖里**不会多出 torch**，也不会连带几 GB 的基础模型 ——
和 `rvc` 后端把重活交给外部进程是同一个思路。

**本项目只定接口、不分发模型**（合规红线）。权重从哪来、合不合法，
由音色包的 `license` / `commercial_ok` / `redistributable` 如实记录。

## 音色包写法（`~/.config/nekopal/voices/<id>/voice.yaml`）::

    id: yumemita-ritsu
    name: "夢限大·律"
    backend: sovits
    license: unofficial
    commercial_ok: false
    redistributable: false

    sovits:
      gpt_weights: 律_GPT_e8.ckpt        # 可选：不写 = 用服务端当前加载的那套
      sovits_weights: 律_SoVITS_e8.pth   # 可选
      ref_audio: 参考.wav                # 必填（3~10 秒最稳）
      prompt_text: "参考音频的逐字文本"     # 必填
      prompt_lang: all_ja                # 必填（GPT-SoVITS 的语言代码）
      text_lang: all_ja                  # 可选，默认跟着 prompt_lang

## 服务契约（我们自己定的一份，刻意做成**无状态**）

    POST {endpoint}/tts
      {"text": …, "text_lang": …, "ref_audio": <绝对路径>, "prompt_text": …,
       "prompt_lang": …, "gpt_weights": <绝对路径|省略>, "sovits_weights": <绝对路径|省略>,
       "speed": 1.0, "seed": -1}
      → 直接回 WAV 字节

    GET  {endpoint}/health  → {"status": "ok", …}

**为什么不用官方 `api_v2.py` 那套 `set_gpt_weights` / `set_sovits_weights`**：
那是**有状态**的（服务端记住"现在加载了谁"），多角色并发时会互相踩。
把权重路径放进请求体里，客户端才是无状态的。
（真要接官方 `api_v2.py`，写个二十行的薄适配就行。）
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nekopal.core.models import (
    Capabilities,
    SynthOptions,
    SynthResult,
)
from nekopal.media import wav_duration, wav_sample_rate

#: 连不上服务时的提示语 —— 写具体一点，省得用户去猜
_HINT = (
    "起服务：python integrations/sovits/serve.py（或设 NEKOPAL_SOVITS_ENDPOINT 指到别处）"
)


class SovitsError(RuntimeError):
    """合成没成 —— 消息里必须能看出**是哪一种没成**（连不上 / 报错 / 没配）。"""


class SovitsBackend:
    id = "sovits"
    name = "GPT-SoVITS（文字 → 声音）"

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._lookup: Optional[Callable[[str], object]] = None

    # -- 装配 ------------------------------------------------------------
    def attach(self, registry: Any, lookup: Callable[[str], object], pack_source: Any = None) -> None:
        """由 Runtime 在装配时调用。

        只需要 ``lookup``（按 voice_id 找音色包）—— 本后端不像 rvc 那样要拿
        注册表跑基础音色。参数表保持和 `rvc.attach` 一致，免得装配处写成两样。
        """

        self._lookup = lookup

    # -- 能力 ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            # 服务只回音频，不产生词级时间戳 —— 前端会退回**整句高亮 / 音量口型**
            word_timings=False,
            rate=True,       # → 映射到请求里的 `speed`
            pitch=False,     # 接口没有音高旋钮：想变调得**换参考音频**
            volume=False,
            streaming=False,
            offline=True,    # 本地服务，不联网
        )

    def available(self) -> bool:
        """**只回答"配了没有"，不回答"活着没有"** —— 这是刻意的，别改成健康探测。

        理由（踩过的坑）：`TTSRegistry.resolve_voice_id` 在某后端
        `available() is False` 时会把音色**悄悄换成 fallback**（见 registry.py
        的 docstring 与 `routes.py` 的读取路径）。如果这里做"服务没起 → False"，
        那么用户选了「夢限大·律」而服务恰好没起时，会被**无声无息地换成系统音色** ——
        听起来像"生效了"，实际完全是另一个声音。这类"静默降级"本项目已经踩遍。

        所以：配了就报 True，**真打不通就在 synth 里大声报错**（见 `_post_tts`）。
        想要"服务到底活着没有"，看 `probe()`（给 /api/backends 之类的显式查询用）。
        """

        return bool(self.endpoint)

    @property
    def endpoint(self) -> str:
        return str(getattr(self._settings, "sovits_endpoint", "") or "").strip().rstrip("/")

    @property
    def _timeout(self) -> float:
        try:
            return float(getattr(self._settings, "sovits_timeout", 300.0))
        except (TypeError, ValueError):
            return 300.0

    # -- 音色 ------------------------------------------------------------
    async def list_voices(self) -> List[Any]:
        # 本后端的音色**全部来自用户自己的音色包**，由 /api/voices 合并时提供
        # （和 rvc 后端一样：后端是能力与实现的提供者，不是音色的提供者）
        return []

    def _pack(self, voice_id: str):
        if self._lookup is None:
            return None
        try:
            return self._lookup(voice_id)
        except Exception:  # noqa: BLE001 - 查不到就当没有
            return None

    @staticmethod
    def _resolve(pack: Any, raw: Optional[str]) -> Optional[Path]:
        """把音色包里的相对路径落到包目录下（也接受写绝对路径）。"""

        if not raw:
            return None
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = Path(str(getattr(pack, "path", ""))) / path
        return path

    # -- HTTP（标准库，不加依赖）------------------------------------------
    async def _get_json(self, path: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_json_sync, path)

    def _get_json_sync(self, path: str) -> Dict[str, Any]:
        url = "{}{}".format(self.endpoint, path)
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - 本地服务
                return json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except Exception as exc:  # noqa: BLE001 - 探测不该抛
            raise SovitsError("连不上 {}（{}）".format(url, exc)) from None

    def probe(self) -> Dict[str, Any]:
        """探一下服务活没活（给显式查询用，**不要**塞进 `available()`）。"""

        try:
            return {"alive": True, "detail": self._get_json_sync("/health")}
        except SovitsError as exc:
            return {"alive": False, "detail": str(exc), "hint": _HINT}

    async def _post_tts(self, payload: Dict[str, Any]) -> bytes:
        return await asyncio.to_thread(self._post_tts_sync, payload)

    def _post_tts_sync(self, payload: Dict[str, Any]) -> bytes:
        url = "{}/tts".format(self.endpoint)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - 本地服务
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                data = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            raise SovitsError(
                "GPT-SoVITS 服务返回 {}：{}{}".format(exc.code, exc.reason, " · " + detail if detail else "")
            ) from None
        except Exception as exc:  # noqa: BLE001
            # 连不上是最常见的一种 —— 单独说清楚，并给出怎么起服务
            raise SovitsError("连不上 {}（{}）。{}".format(url, exc, _HINT)) from None

        if not data:
            raise SovitsError("GPT-SoVITS 服务返回了空音频（看看服务端日志）")
        if not data[:4] == b"RIFF":
            raise SovitsError(
                "GPT-SoVITS 服务回来的不是 WAV（前 16 字节：{!r}）—— "
                "检查服务端日志".format(data[:16])
            )
        return data

    # -- 合成 ------------------------------------------------------------
    def _payload(self, pack: Any, text: str, options: SynthOptions) -> Dict[str, Any]:
        ref = self._resolve(pack, getattr(pack, "ref_audio", None))
        if ref is None:
            raise SovitsError("音色包 `{}` 没有写 `sovits.ref_audio`".format(getattr(pack, "id", "?")))
        if not ref.is_file():
            raise SovitsError("参考音频不存在：{}".format(ref))
        prompt_text = getattr(pack, "prompt_text", None)
        if not prompt_text:
            raise SovitsError(
                "音色包 `{}` 没有写 `sovits.prompt_text`（参考音频的逐字文本，必填）".format(
                    getattr(pack, "id", "?")
                )
            )

        prompt_lang = getattr(pack, "prompt_lang", None) or "zh"
        payload: Dict[str, Any] = {
            "text": text,
            "text_lang": getattr(pack, "sovits_text_lang", None) or prompt_lang,
            "ref_audio": str(ref),
            "prompt_text": str(prompt_text),
            "prompt_lang": prompt_lang,
            # ⚠️ speed 由 `options.rate` 来 —— 和别的后端一致，用户只用调一个旋钮
            "speed": float(options.rate),
            "seed": -1,
        }
        gpt = self._resolve(pack, getattr(pack, "sovits_gpt_weights", None))
        sovits = self._resolve(pack, getattr(pack, "sovits_weights", None))
        if gpt is not None:
            payload["gpt_weights"] = str(gpt)
        if sovits is not None:
            payload["sovits_weights"] = str(sovits)
        return payload

    async def synth(self, voice_id: str, text: str, options: SynthOptions) -> SynthResult:
        if not self.endpoint:
            raise SovitsError("没有配置 GPT-SoVITS 服务地址（`NEKOPAL_SOVITS_ENDPOINT`）。{}".format(_HINT))

        pack = self._pack(voice_id)
        if pack is None:
            raise SovitsError(
                "找不到 {} 对应的音色包（放 ~/.config/nekopal/voices/ 下）".format(voice_id)
            )

        payload = self._payload(pack, text, options)
        data = await self._post_tts(payload)

        return SynthResult(
            audio=data,
            sample_rate=wav_sample_rate(data),
            duration=wav_duration(data),
            # 服务不回时间戳 → 空数组。客户端必须容忍空 words（退回句级高亮 / 音量口型），
            # 这也是 `capabilities().word_timings=False` 想表达的事。
            words=[],
            media_type="audio/wav",
            extension="wav",
        )

    async def aclose(self) -> None:
        return None
