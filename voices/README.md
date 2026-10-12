# 音色包目录

本目录存放 NekoPal 的**音色包**。每个音色是这里的一个子文件夹。

> **重要：本仓库不含任何音色素材。**
> 出于版权考虑，项目只提供**规范**与**加载机制**，素材一律由使用者自行准备。
> 详见 [`../docs/设计文档.md`](../docs/设计文档.md) 第 8 章「风险与合规」与 [`../docs/竞品与合规调研.md`](../docs/竞品与合规调研.md)。
>
> 🚫 **项目只做接口，不做分发**：不内置、不下载、不提供第三方模型的下载入口。
> ✅ **默认音色全部来自明确可用的来源**（系统 `say` / Edge-TTS 官方音色 / VOICEVOX）。

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
- ✅ 推荐来源：自己录音、明确授权（如 CC0）的公开语音数据集。
- 如果你的音色包会公开分发，请确保 `license` 字段如实填写。
