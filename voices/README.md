# 音色包目录

本目录存放 NekoPal 的**音色包**。每个音色是这里的一个子文件夹。

> **重要：本仓库不含任何音色素材。**
> 出于版权考虑，项目只提供**规范**与**加载机制**，素材一律由使用者自行准备。
> 详见 [`../docs/设计文档.md`](../docs/设计文档.md) 第 8 章「风险与合规」与 [`../docs/竞品与合规调研.md`](../docs/竞品与合规调研.md)。
>
> 🚫 **项目只做接口，不做分发**：不内置、不下载、不提供第三方模型的下载入口。
> ✅ **默认音色全部来自明确可用的来源**（系统 `say` / Edge-TTS 官方音色 / VOICEVOX）。

## 放哪里

```
~/.config/nekopal/voices/<音色 id>/voice.yaml     ← 推荐（在仓库外，天然不会被提交）
<repo>/voices/<音色 id>/voice.yaml                ← 也可以（素材已被 .gitignore 排除）
```

## 用命令行管理

```bash
nekopal packs dir                     # 目录在哪（加 --open 会在 Finder 打开）
nekopal packs init voice my-voice     # 生成带注释的模板
nekopal packs list                    # 扫到了什么、哪些被跳过了、为什么
```

坏包**不会**让服务启动失败：会被跳过并在 `nekopal packs list` 里逐条列出原因。

## 变声音色（RVC / DDSP-SVC 这类"音频 → 音频"模型）

社区动漫角色的音色模型**大多是 RVC**（声音转换），不是 GPT-SoVITS（文本合成）——
它们**不能直接喂文本**。所以本项目把它们做成一条组合管线：

```
文本 ──(base_voice 合成)──► 音频 ──(你配置的变声命令)──► 目标音色
```

**① 先配好变声命令**（占位符 `{input}` `{output}` `{model}` `{index}` `{pitch}`）：

```bash
export NEKOPAL_RVC_COMMAND='python infer.py {input} {output} --model {model} --index {index}'
nekopal convert show      # 看当前配置
nekopal convert test      # 自检：合成 → 变声 → 报告（附排查清单）
```

**② 再写音色包**：

```yaml
id: my-converted
name: "我的变声音色"
backend: rvc                    # ← 关键
lang: ["zh"]
tags: ["community"]
license: unofficial             # 未获公开授权的模型如实标 unofficial（界面会打黄标）
commercial_ok: false            # 别替原作者许诺，自己按实际情况填
redistributable: false
rvc:
  base_voice: edge:zh-CN-XiaoxiaoNeural   # 先用谁合成（必填）
  model: voice.pth                         # 你自己的变声模型（必填，放本目录）
  index: voice.index                       # 可选（有些模型不带 index）
  # command: [...]                         # 可选，覆盖全局命令
```

**③ 验证**：`nekopal packs list` 看到它 → `nekopal convert test --pack my-converted` → 通了就能在跟读窗里选。

> ⚠️ 变声不产出**词级时间戳**，所以高亮会自动降级为**句级**（这是正常的，不是坏了）。

> 💡 音色只负责「声音」。如果你还想让声音**有个形象**（Live2D + 口型），请看 [`../characters/`](../characters/README.md)——
> 角色包会**引用**这里的音色包，而不是重复定义一份音色。

---

## 目录结构

```
voices/
└── <音色 id>/
    ├── voice.yaml      # 必填：音色定义
    ├── ref.wav         # 仅 sovits 后端需要（参考音频 3~10 秒）
    └── ckpt/           # 可选：微调权重（sovits）
```

## `voice.yaml` 规范

```yaml
id: my-voice                    # 必填，需与目录名一致
name: "示例音色"                 # 必填，界面显示名
backend: sovits                 # 必填：say | edge | sovits | kokoro
lang: ["zh", "en"]              # 语言标签
tags: ["cloned"]                # 自由标签
license: "self-recorded"        # 必填：授权等级，见 ../docs/设计文档.md#47
commercial_ok: true             # 必填：能否用于商业场景
redistributable: false          # 必填：能否随作品/项目再分发
description: "由本人录音克隆"

sovits:
  ref_audio: ref.wav
  prompt_text: "参考音频对应逐字文本"
  prompt_lang: zh
  weights: ckpt/e10.ckpt        # 可选

edge:
  voice: zh-CN-XiaoxiaoNeural

say:
  voice: Tingting
```

## 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 音色唯一标识，与目录名一致 |
| `name` | 是 | 界面上显示的名字 |
| `backend` | 是 | 使用哪个 TTS 后端，必须是已注册的后端 id |
| `lang` | 否 | 语言标签，用于筛选 |
| `tags` | 否 | 自由标签 |
| `license` | **是** | **授权等级**：`official` / `self-recorded` / `licensed` / `public-domain` / `unofficial`（分级说明见 [`../docs/设计文档.md`](../docs/设计文档.md#47-音色与角色的授权模型)） |
| `commercial_ok` | **是** | 能否用于商业场景 |
| `redistributable` | **是** | 能否随作品/项目再分发 |
| `description` | 否 | 简介 |
| 后端专属段 | 否 | 只填该后端需要的字段 |

## 加载规则

1. 启动时扫描本目录下的每个子文件夹，读取 `voice.yaml`。
2. 缺少必填字段、`backend` 未注册、或素材文件不存在时：**跳过该音色并在界面提示**，不影响服务启动。
3. 音色 ID 对外表示为 `backend:local_id`，例如 `sovits:my-voice`。

## 合规提醒

- ❌ 请勿把**受版权保护的动漫/影视/名人语音素材**提交到本仓库或公开分享。
- ✅ 推荐来源：自己录音、明确授权（如 CC0）的公开语音数据集；或**利用規約明确的免费音声素材角色**
  （例如「つくよみちゃん」——其規約允许二次使用。调研发现这类合法音源反而比未授权同人更流行，见 §4.4）。
- 📌 本项目**不提供**任何第三方模型的下载入口或清单 —— 素材请自行准备，并自行确认授权。
- 如果你的音色包会公开分发，请确保 `license` 字段如实填写。
