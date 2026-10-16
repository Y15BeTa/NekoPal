# GPT-SoVITS 接入（NekoPal × GPT-SoVITS）

把 GPT-SoVITS 这类「**文字 → 目标音色**」的端到端 TTS，接成 NekoPal 的音色。

```
文本 ──serve.py（常驻 GPT-SoVITS 引擎）──► 目标音色的朗读 ──► 播放 / 导出
```

**本项目不提供任何模型、也不分发权重。** 引擎与权重请自行准备（自用），
并如实填写音色包里的 `license` / `commercial_ok` / `redistributable`。

---

## 它和 `integrations/rvc/` 的分工（这是它存在的理由）

| | **RVC**（变声） | **GPT-SoVITS**（本目录） |
| --- | --- | --- |
| 本质 | 音频 → 音频 | **文字 → 声音** |
| 语气 / 断句 / 重音 | **全部来自 base 音色**（如晓伊） | **来自权重 + 参考音频** |
| 它做什么 | 只换"嗓子" | 连"怎么说"一起学 |
| 想换语气 | 只能调 base 的语速 / 音高 | **换一段参考音频** ✅ |

> 换句话说：RVC 出来的"要楽奈"其实**是晓伊在念**，只是戴了她的嗓子；
> GPT-SoVITS 出来的语气**是她自己的**。两条链**并存、可切换**，谁也不替代谁。

---

## 一、安装引擎（**已在本机完成**，此处留档）

```bash
# ① 拉 GPT-SoVITS 本体（放仓库外 —— 它是 GB 级第三方，不进 NekoPal 仓库）
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git \
    ~/.local/share/nekopal/sovits

# ② 独立 venv（⚠️ 和 RVC 一样用 Python 3.11，3.12 上 numpy 会编译失败）
#    ⚠️ 还要先修 SDKROOT —— 本机默认 SDK（MacOSX27）是坏的
export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk
uv venv ~/.local/share/nekopal/sovits-venv --python 3.11
uv pip install --python ~/.local/share/nekopal/sovits-venv/bin/python \
    -r ~/.local/share/nekopal/sovits/requirements.txt

# ③ 基础模型（推理必需的三个；uvr5 人声分离用不到，不下）
bash integrations/sovits/download_models.sh   # 见下方「下载了什么」
```

### 三个坑（都不是 GPT-SoVITS 的问题，和 RVC 那边同源）

| 坑 | 症状 | 修法 |
| --- | --- | --- |
| 本机 SDK 坏了 | `ld: tapi error: malformed file` | `export SDKROOT=…/MacOSX26.sdk` |
| Python 3.12 | `numpy` 编译失败 | 用 **3.11** |
| torch 2.6+ 改了 `weights_only` | 加载旧 ckpt 被拒 | 设 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1`（`serve.py` 里已设） |

### 下载了什么（位置很关键，放错就找不到）

来源：`huggingface.co/XXXXRT/GPT-SoVITS-Pretrained`（国内可用 `hf-mirror.com`）

| 文件 | 解到 | 干什么用 |
| --- | --- | --- |
| `pretrained_models.zip` | `GPT_SoVITS/` | **必需**：`chinese-hubert-base` + `chinese-roberta-wwm-ext-large` + `sv/…eres2netv2` |
| `G2PWModel.zip` | `GPT_SoVITS/text/` | **必需**：中文多音字 |
| `open_jtalk_dic_utf_8-1.11.tar.gz` | **`pyopenjtalk` 包自己的目录里** | **必需（日语）**：日语 G2P 词典 |
| `nltk_data.zip` | venv 的 `sys.prefix` | 英文 g2p 用，可让 `g2p_en` 自己按需下 |
| ~~`uvr5_weights.zip`~~ | — | 人声分离，**本用途不需要** |

> ⚠️ `open_jtalk` 词典**必须**放进 `site-packages/pyopenjtalk/` 里 ——
> 代码是从**包自己的目录**找它的（`japanese.py`），放别处等于没放。

---

## 二、起服务（**必须**，不然每句都要重读 300 MB）

```bash
~/.local/share/nekopal/sovits-venv/bin/python integrations/sovits/serve.py
```

- 默认监听 `127.0.0.1:9880` —— **正是 NekoPal 的 `sovits_endpoint` 默认值**，
  所以不用额外设置 ✅
- 起来时会**预热引擎**（几十秒）；之后每句只走一次 HTTP
- 自检：`curl http://127.0.0.1:9880/health`
- 换音色时才切权重（GPT 148 MB + SoVITS 165 MB，切一次要几秒）；同一音色连读时**不重复切**

常用参数：`--port` / `--device cpu|mps` / `--version v2ProPlus` / `--no-warmup`
（**`--version` 必须和权重的版本一致**，交付包通常是 `v2ProPlus`）

---

## 三、导入音色包

如果你手上的是一份 **GPT-SoVITS 交付包**（有 `模型索引.json` + 每角色的
`模型/` 与 `推荐参考/`），一条命令就能全导进来：

```bash
# 先看看会生成什么
python integrations/sovits/import_pack.py ~/Downloads/<你的交付包> --dry-run

# 真的生成（权重默认**软链接**，不占额外空间）
python integrations/sovits/import_pack.py ~/Downloads/<你的交付包>
```

之后它们会**自动出现在界面的音色下拉列表里**（音色列表本来就是后端
从 `~/.config/nekopal/voices/` 读的，前端零改动）。

手写一个也可以：

```yaml
# ~/.config/nekopal/voices/my-clone/voice.yaml
id: my-clone
name: "我的克隆音色"
backend: sovits
lang: ["ja"]
tags: ["cloned"]
license: self-recorded          # ⚠️ 如实填，这是合规的核心
commercial_ok: false
redistributable: false

sovits:
  gpt_weights: 模型/xxx_GPT_e8.ckpt        # 可选：不写 = 用服务端当前加载的
  sovits_weights: 模型/xxx_SoVITS_e8.pth   # 可选
  ref_audio: ref.wav                       # 必填：3~10 秒参考音频
  prompt_text: "参考音频的逐字文本"           # 必填：少它音出来会"飘"
  prompt_lang: all_ja                      # 必填：all_ja / zh / en …
  text_lang: all_ja                        # 可选，默认跟着 prompt_lang
```

> **零样本克隆**：没有训练好的权重时，只写 `ref_audio` / `prompt_text` / `prompt_lang`
> 三行就行 —— 服务端用自己当前加载的基础权重 + 你给的那段参考音频。

---

## 四、语气怎么调（**这才是它比 RVC 强的地方**）★

**换语气 = 换一段参考音频**，不用调任何参数。

GPT-SoVITS 会**模仿你给的那段参考音频的语气**。所以：

| 想要的感觉 | 怎么做 |
| --- | --- |
| 平静日常 | 换一条"平静"的参考（如「もうすぐ来ますよ。」） |
| 礼貌询问 | 换一条疑问语气的参考 |
| 温和开心 | 换一条笑着说的参考 |
| 认真劝说 | 换一条语气重的参考 |

好一点的交付包会**按语气分组**给你一批参考（`推荐参考/<语气组>/`），
`import_pack.py` 默认挑交付包自己标的那组；
想换就改 `voice.yaml` 里的 `ref_audio` + `prompt_text` 两行（**必须成对改**）。

> 💡 还可以做成"NekoPal 按上下文自动挑参考"（回答用疑问语气、提醒用认真语气…）——
> 那是后端契约要扩展的事，见 `需求与想法.md` 的 R24/R25。

---

## 五、排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| **选了音色但报「连不上」** | 服务没起 | 起 `serve.py`。**这是刻意的** —— 绝不静默降级回系统音色 |
| `缺少基础模型：chinese-hubert-base` | 模型没下 / 位置不对 | 见上表，`pretrained_models.zip` 要解到 `GPT_SoVITS/` |
| 日语出来的音是乱读的 | `open_jtalk` 词典没放进 `pyopenjtalk` 目录 | 见上表 |
| `WeightsUnpickler … weights_only` | torch 2.6+ 的默认值变了 | `serve.py` 已设 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` |
| 声音"飘" / 忽快忽慢 | `prompt_text` 和参考音频对不上 | 逐字核对；**换参考音频必须同时改 `prompt_text`** |
| 首次请求特别慢 | 在切权重 / 首次加载 | 正常，之后同一音色就快了 |
| 进程静默退出 | 查崩溃报告 | `~/Library/Logs/DiagnosticReports/*.ips`（RVC 那边就是这样定位到 `libomp` 的） |

### 为什么"连不上"要报错而不是偷偷换音色

`TTSRegistry.resolve_voice_id` 在某后端 `available() is False` 时**会把音色换成 fallback**。
所以 `sovits` 后端的 `available()` **故意只回答"配了没有"、不做健康探测** ——
否则"选了克隆音色但服务恰好没起"会**无声无息变成系统音色**，
听起来像生效了，其实完全是另一个声音（本项目踩过一整批这种静默降级）。

宁可**大声报错**，也不假装成功。

---

## 六、性能（Apple Silicon）

**待实测**（引擎装好后补上）。

参考：GPT-SoVITS 在 CPU 上是**明显慢于实时**的（这也是为什么 R16 的
"挂机自发生成"必须允许慢、不能当点击的即时反馈用）。
`serve.py` 常驻 + NekoPal 的**预合成队列**能掩盖掉绝大部分等待。

> ⚠️ Apple Silicon 上先别急着上 `--device mps`：MPS 对 GPT-SoVITS 的算子覆盖不全，
> 可能报错或出怪声。**先用 `cpu` 跑通**，再拿 A/B 比。
