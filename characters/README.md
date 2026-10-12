# 角色包目录

本目录存放 NekoPal 的**角色包**。一个角色 = **音色** + **形象（Live2D）** + **可选人设**。

> **重要：本仓库不含任何素材。**
> 出于版权考虑，项目只提供**规范**与**加载机制**，音色与 Live2D 模型一律由使用者自行准备。
> Live2D 有**双重授权**需要注意，详见下方「合规提醒」。
>
> 🚫 **项目只做接口，不做分发**：不内置、不下载、不提供第三方模型的下载入口。
> 完整调研见 [`../docs/竞品与合规调研.md`](../docs/竞品与合规调研.md) 第 10 节。

## 放哪里

```
~/.config/nekopal/characters/<角色 id>/character.yaml   ← 推荐（在仓库外，天然不会被提交）
<repo>/characters/<角色 id>/character.yaml              ← 也可以（素材已被 .gitignore 排除）
```

## 用命令行管理

```bash
nekopal packs dir                        # 目录在哪（加 --open 会在 Finder 打开）
nekopal packs init character my-char     # 生成带注释的模板
nekopal packs list                       # 扫到了什么（含「形象就绪 / 仅声音」状态）
```

## 想立刻有个合法形象？可从这些地方拿

> 以下均为**官方明确授权**的来源。**本项目不代下载、不打包**，请你自行前往并遵守其条款。

| 来源 | 说明 |
| --- | --- |
| **Live2D 官方免费样例** | 20+ 个角色，官方适用场景含 `For avatars`；**须标注版权**；`Tororo & Hijiki`（两只猫）与 `Kei`（口型同步示范）特别适合本项目 |
| **ずんだもん** | Live2D 官方样例 + VOICEVOX 音色，**是唯一"声音与形象都合法"的动漫角色组合** |
| **你自己画 / 委托画** | 最干净，而且独一无二 |
| **VRoid Hub** | 3D（VRM）路线，模型许可普遍更宽松，逐个模型标注 |
| **nizima / BOOTH** | 付费商店，买断后按商品页的商用许可使用 |

⚠️ **两条最容易踩的线**：
1. **「配布」≠「再配布」** —— 发你用模型做的作品可以，**把模型文件打包分发不行**。
2. **社区分享的动漫角色模型绝大多数是未授权同人** —— 自用风险低，但发视频 / 打包 / 商用风险急剧上升。

---

## 目录结构

```
characters/
└── <角色 id>/
    ├── character.yaml
    └── model/                        # Live2D 模型（Cubeism 3/4/5 runtime 文件）
        ├── xxx.model3.json
        ├── xxx.moc3
        ├── *.motion3.json
        └── *.exp3.json
```

## `character.yaml` 规范

```yaml
id: my-character
name: "示例角色"
voice: my-voice                 # 引用音色包 id（见 ../voices/）
license: "self-recorded"        # 必填：授权等级，见 ../docs/设计文档.md#47
commercial_ok: true             # 必填
redistributable: false          # 必填
description: "自用角色"

avatar:
  type: live2d
  model: model/xxx.model3.json
  scale: 1.0
  lipsync:
    enabled: true
    mode: volume                # volume（音频振幅，通用）| timings（词级时间戳，更准）
    param: ParamMouthOpenY
  expressions:                  # 可选：情绪 → 表情映射
    idle: exp_00
    speaking: exp_01

persona:                        # 可选（Phase 4：角色化改写）
  prompt: "你是一位……"
  catchphrase: ["这一段有点难，我慢慢念。"]
```

## 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 与目录名一致 |
| `name` | 是 | 界面显示名 |
| `voice` | 是 | **引用**音色包的 `id`（不重复定义音色） |
| `license` | **是** | **授权等级**：`official` / `self-recorded` / `licensed` / `public-domain` / `unofficial` |
| `commercial_ok` | **是** | 能否用于商业场景 |
| `redistributable` | **是** | 能否随作品/项目再分发 |
| `avatar.type` | 否 | 目前仅 `live2d` |
| `avatar.model` | 否 | 相对本目录的模型路径（`.model3.json`） |
| `avatar.lipsync.mode` | 否 | `volume`（通用）/ `timings`（需后端支持 `word_timings`） |
| `persona` | 否 | 人设与口头禅，Phase 4 才使用 |

## 加载规则

1. 启动时扫描本目录下的每个子文件夹，读取 `character.yaml`。
2. 缺 `voice` 指向的音色、或模型文件不存在时：**降级为「无形象的音色」**，朗读不受影响，并在界面提示。
3. 角色层未启用（Phase 1/2）时，本目录直接忽略，不报错。

## 合规提醒

⚠️ **Live2D 是双重授权**，请确认两件事：

1. **Live2D Cubism SDK 授权**：SDK 本身有独立的使用条款（个人 / 小规模通常免费，但需同意条款）。
2. **模型文件版权**：模型是**美术资产**，有自己的版权与使用条款。例如公开项目里的示例模型，通常是 Live2D 公司提供的样例数据，**不适用项目自身的开源许可**。

因此：

- ❌ 请勿把未授权的角色模型提交到本仓库或公开分享。
- ✅ 推荐来源：自绘 / 委托绘制 / 明确允许再分发的模型。
- `license` / `commercial_ok` / `redistributable` 字段请如实填写（分级见 [`../docs/设计文档.md`](../docs/设计文档.md#47-音色与角色的授权模型)）。
- 🚫 本项目**不提供**任何 Live2D 模型的下载入口或清单。

音色部分的合规要求见 [`../voices/README.md`](../voices/README.md)。
