# NekoPal · 猫伴

[![CI](https://github.com/Y15BeTa/NekoPal/actions/workflows/ci.yml/badge.svg)](https://github.com/Y15BeTa/NekoPal/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

> 让一个你喜欢的角色，把长文读给你听。
> 一整块内容丢给它 —— 它开始念，文字跟着声音逐句高亮、自动往下滚；它还能趴在桌面角落陪着你，把讲义变成通勤路上听的播客。

**NekoPal** 是一个本地运行的「**角色化朗读器**」。它把屏幕上任意位置的一整块文字，变成**能跟读的语音**，并且由**你自己选的角色**（Live2D 形象 + 克隆音色）来念。
它是「朗读工具」和「桌面陪伴」的交叉点：**不是聊天机器人，而是替你读书的伙伴。**

![NekoPal 跟读窗](docs/images/reader-demo.png)

> 上图：朗读进行中。前两句已读完（变灰），当前句带活动边栏并自动居中；文字先渲染、声音随后跟上。

**当前状态**：已经是一个**可双击打开的原生应用**（`nekopal app`）—— opencode 回答完**自动开始朗读，不需要点任何东西**
（`say` 离线 + Edge-TTS 联网，含词级高亮）；**变声（RVC）链路也已打通**，且变声音色同样有**词级高亮**。
下一步是「文件模式 / 进度书签」，详见下面[路线图](#路线图)。

---

## 为什么需要它

- **AI 让长文泛滥了**：一句话能说清的事，模型给你写 800 字。读完很累，但**闭上眼听**就还好。
- **讲义/文档想在路上听**：通勤、走路、做家务时，「听」是唯一能并行消费信息的方式。
- **现有方案各缺一块**：系统朗读没有跟读高亮、不能换音色；网页朗读插件进不了本机应用；TTS 阅读器（Speechify 等）没有角色、没有桌宠；VTuber 陪伴应用只会**聊天**，不会**朗读你的长文**。
- **中英混排是重灾区**：`attention`、`tokenizer`、`LoRA` 这类术语，很多中文语音会读坏。

NekoPal 的定位是：**整块朗读 + 跟读高亮 + 角色化身 + 离线优先**。

---

## 四个招牌玩法

| # | 玩法 | 一句话 |
| --- | --- | --- |
| 1 | **角色化朗读** | 你的 Live2D 角色读给你听，有口型、有表情 —— 而不是一个冰冷的机器音 |
| 2 | **自动续读（Follow）** | AI / 文件一有新内容就接着念，像电台播报；不用每次重新点 |
| 3 | **桌宠伴读** | 角色趴在屏幕角落，随时投喂内容就开读 |
| 4 | **通勤播客导出** | 一键把整份讲义 / 整条长回答导出 mp3，路上听 |

---

## 能力清单

| 特性 | 说明 | 状态 |
| --- | --- | --- |
| **整块抓取** | 一整个长回答 / 整篇文档 / 选定内容块，而不是一次一个词 | ✅ 可用 |
| **跟读高亮窗** | 独立阅读窗，逐词/逐句高亮 + 自动居中滚动 + 点任意句从那里读 | ✅ 可用 |
| **可插拔 TTS 后端** | `macOS say`（离线兜底）/ `Edge-TTS`（音色多）/ `GPT-SoVITS`（声音克隆） | ✅ say + edge（506 个音色） |
| **中英混排切分 + 读前清洗** | 跳过代码块与表格、`→` 读作"箭头"、术语按英文发音 | ✅ 可用 |
| **预合成队列 + 音频缓存** | 播第 N 句时合成第 N+1 句；相同内容只合成一次 | ✅ 可用 |
| **播放参数** | 语速实时切换（前端 `playbackRate`）；音量/音调（Edge-TTS） | ✅ 语速（0.75–2.0x） |
| **自动续读 Follow（边写边读）** | 助手**一边写、一边念** —— 不用等整篇生成完 | ✅ 可用（`NEKOPAL_FOLLOW=0` 可关） |
| **opencode 自动朗读** | 回答完自动推给跟读窗，无需复制粘贴；**可在控制面板开关** | ✅ **已打通** |
| **活动记录 `/api/activity`** | 记录推送来源与插件事件，出事一条命令定位 | ✅ 可用 |
| **进度书签** | 记住读到哪，下次接着读 | 🔜 Phase 2 |
| **原生窗口外壳** | `NekoPal.app`（推荐）/ `nekopal up`：不开浏览器、**无需点击**即可自动朗读 | ✅ 可用（**关窗即全关，不占开机时间**） |
| **导出音频** | `nekopal export`：wav / m4a **零额外依赖**（mp3 需 ffmpeg） | ✅ 可用 |
| **音色包 / 角色包（只做接口）** | `nekopal packs`：扫描、校验、模板；**不内置任何素材** | ✅ 可用 |
| **变声后端（RVC / DDSP-SVC）** | `nekopal convert`：`base_voice 合成 → 变声命令`，实测跑通自备的社区 RVC 音色 | ✅ **可用**（常驻服务下 RTF 0.39） |
| **开机自启** | `nekopal autostart install`（macOS LaunchAgent） | ✅ 可用（**默认不装**） |
| **文件模式** | 打开 `.md`，跟读窗自渲染整篇 | 🔜 Phase 2 |
| **控制面板（设置 / 导出 / 聊天 / 活动）** | 主窗底部标签栏：**常用开关不用再改配置文件** | ✅ 可用 |
| **可插拔音色包 / 角色包** | 音色与角色 = 素材 + 规范；仓库内**不含任何受版权保护的素材** | 🔜 音色包 Phase 3 |
| **角色层：Live2D** | 用 **Live2D 官方 Cubism 5 框架**渲染角色（自备模型 ✗），支持动作 / 表情 / 物理 | ✅ **可用** |
| **口型同步** | 主窗 WebAudio 算电平 → 后端中转 → `ParamMouthOpenY` 张嘴 | ✅ **可用** |
| **桌宠模式** | 原生 **NSPanel**：**能浮在所有全屏窗口之上** · 透明 · 无边框 · 可拖动 · 位置/尺寸记忆 | ✅ **可用** |
| **精确命中（点穿）** | 只有角色和按钮能点，其余透明区域**点穿**到桌面（读光标位置，**免授权**） | ✅ **可用** |
| **角色交互** | 悬停浮现半透明按钮 · 点击换动作 · 自动眨眼 · 右键菜单（**换装 / 动作 / 表情 / 缩放 / 退出**） | ✅ **可用** |
| **主窗集成** | 顶栏「形象」下拉（与角色窗**双向同步**）；**点开 App 就带人物**，关窗一起关 | ✅ **可用** |
| **点击交互** | 点一下角色：**随机动作 + 随机表情 + 随机说一句**（用她自己的音色） | ✅ **可用** |
| **角色聊天（大模型）** | 按**角色性格 + 你的私人信息**现编一句再 TTS；`api_key` 只存本机、永不入仓库 | ✅ **可用**（不配也有静态台词兜底） |
| **全局划词快捷键** | 辅助功能 API + 全局热键 | 🔜 可选 |

---

## 🎛️ 控制面板

主窗底部是**标签栏**，点开哪块看哪块：

| 标签 | 里面有什么 |
| --- | --- |
| **⚙️ 设置** | ☑ **自动朗读 opencode 输出** · ☑ 边写边读 · 自动滚动 · 预取句数 · 缓存上限 · **清空缓存** |
| **💾 导出** | 把「这次朗读」或「输入框里的文字」导成 wav / m4a / aac / mp3 |
| **🤖 角色聊天** | 大模型台词的状态与配置入口（见下一节） |
| **📋 活动记录** | 最近推送与插件事件（排查用） |

**改设置不用再编辑 `~/.config/nekopal/*.json`** —— 改完即时生效，并写进 `ui.json`，重开 App 也还在。

> 💡 **自动朗读开关**（`auto_read`）
> 关掉之后，**插件推来的内容只被记录、不发声**；
> 你自己在界面上点「朗读」照常播 —— 所以它只拦"外部推送"，不拦你。

---

## 🤖 让角色自己说话（可选）

默认点一下角色会说**固定的 6 句**。配上一个大模型之后，它会按
**角色性格 + 你的私人信息**现编一句，再交给 TTS —— **说的还是你自己那个音色** 🐱

### 三个文件（都在**仓库外**，不用改代码）

| 文件 | 作用 |
| --- | --- |
| `~/.config/nekopal/chat.yaml` | 大模型配置：`base_url` / **`api_key`** / `model` |
| `~/.config/nekopal/profile.md` | 你的私人信息（称呼、近况、在意的事…） |
| `~/.config/nekopal/characters/<角色 id>/persona.md` | 角色性格（没写就**退回仓库里的示例**） |

> 前两个文件**第一次跑起来就会自动生成模板** ✗。
> 主窗底部的「**角色聊天**」那一栏可以**一键打开它们所在的文件夹** ✗。

### 三步

1. 打开主窗 → 展开底部「**角色聊天**」→ 点「**打开配置文件夹**」
2. 编辑 `chat.yaml`，填三行 ✗：

   ```yaml
   base_url: "https://api.deepseek.com"   # 不要带 /chat/completions
   api_key: "sk-..."                      # 你自己的
   model: "deepseek-flash"                # DeepSeek-V4.1-Flash
   ```

   > 上面这三行是**实测跑通**的一份 ✗（`deepseek-flash` 是官方 `/models` 里给出的 ID ✗）。
   > 用别的 OpenAI 兼容网关也行 ✗ —— 把 `base_url` 换成它的根地址、
   > `model` 换成它在 `/models` 里列出的 ID 即可 ✗。
   >
   > 💡 想确认模型 ID：直接 `curl https://api.deepseek.com/models -H "Authorization: Bearer <key>"` ✗

3. **关掉 App 再打开**（配置在启动时读取）

### 不配也能用

**完全不用配** ✗ —— 没配（或调用失败）时会**自动退回**角色包里的静态台词 ✗，
接口回 `source: "static"`，日志里也写清原因 —— **不会静默失效** ✅

### ⚠️ 两条红线

1. **`api_key` 绝不要提交到任何仓库** ✗ ——
   `chat.yaml` 在 `~/.config/` 下（**天然不在项目目录里** ✗），写入时权限是 `600`（只有你能读 ✗）。
   项目的接口**只回报「有没有 key」** ✗，**不会返回 key 本身** ✗，日志里也不会出现 ✗。
2. **`profile.md` 里别放不该外发的东西** ✗ ——
   它的内容会被**塞进提示词发给模型** ✗。

> 💡 想自己写角色性格？抄 `characters/example-rana/persona.md` ✗ ——
> 最能影响生成风格的是里面「**说话方式**」和「**口头禅**」两节 ✗。

---

## 快速开始

```bash
# 💡 建议装在家目录下（如 ~/NekoPal）。
#    macOS 把「桌面 / 文稿 / 下载」当作保护目录 —— 项目放在那里，
#    每次 App 访问都要你先点一次「允许」；放在家目录就没这回事。
git clone https://github.com/Y15BeTa/NekoPal.git ~/NekoPal
cd ~/NekoPal

uv venv --python 3.12
uv pip install -e ".[edge,dev,desktop]"   # desktop = 原生窗口外壳；不装也能用浏览器
```

### macOS：装成一个「真应用」（推荐）

```bash
./scripts/install_macos_apps.sh
```

会在 `~/Applications/` 生成 **`NekoPal.app`**（自带图标，**Launchpad 与聚焦搜索里都能找到**）：

- **打开**：点一下就行 —— 变声服务（若已配置）→ 后端 → 原生窗口，一步到位
- **关闭**：**关掉窗口就够了** —— 后端与变声服务会一起停，
  所以**只需要一个 App**，不用再配一个「关闭」App
- 卸载：`./scripts/install_macos_apps.sh --remove`

> ⚠️ **第一次打开时**，macOS 会问「是否允许访问『文稿』文件夹」—— 点**允许**即可。
> 这是因为**项目装在 `~/Documents`**（macOS 的保护目录），不是 App 在偷看你的文件。
> **装在家目录（如 `~/NekoPal`）就不会有这一步。**

### 其他方式

| 方式 | 命令 | 特点 |
| --- | --- | --- |
| **应用**（推荐） | `install_macos_apps.sh` | 双击即用；**关窗即全关** |
| **置顶小条** | `nekopal pet` | 透明置顶小卡片，**点一下就暂停/继续**（桌宠的第一步） |
| 双击脚本 | `启动猫伴.command` | 不装 App 也能用；会闪一个终端窗口 |
| 一键启动 | `nekopal up` | 命令行版，同样是「**关窗即全关**」 |
| 只要窗口 | `nekopal app` | 自动拉起后端（不含变声服务） |
| 只要浏览器 | `nekopal serve` | 零额外依赖；需点一次「启用自动朗读」 |
| 全部停掉 | `nekopal down` | 窗口 + 后端 + 变声服务 |

> **为什么不设开机自启**：变声服务带 torch，常驻要几百 MB —— 不值得为它拖慢开机。
> 所以做成「**要用才点**」。`nekopal autostart` 仍然可用（可选），但默认不装。

命令行也可以直接读：

```bash
uv run nekopal voices                      # 列出可用音色（含你自己导入的包）
uv run nekopal read "把这段文字读给我听"    # 直接在本机播放
uv run nekopal export --file 讲义.md       # 导出音频（通勤播客）
uv run nekopal packs list                  # 音色包 / 角色包（只做接口，不内置素材）
uv run nekopal convert test                # 变声链路自检（RVC / DDSP-SVC）
uv run nekopal autostart install           # 开机自启（macOS LaunchAgent）
```

**音色包 / 角色包（只做接口，不内置素材）**：

```bash
uv run nekopal packs dir                    # 我的包放哪（在仓库外，天然安全）
uv run nekopal packs init voice my-voice    # 生成音色包模板
uv run nekopal packs init character my-char # 生成角色包模板
uv run nekopal packs list                   # 扫到了什么、哪些被跳过了
```

> 📌 **本项目只提供加载接口，不打包任何音色 / Live2D 素材。** 素材放
> `~/.config/nekopal/voices/` 与 `~/.config/nekopal/characters/`（**在仓库外**），
> 或仓库内的 `voices/`、`characters/`（素材已被 `.gitignore` 排除）。
> 想用什么形象/音色由你决定，合规责任也在你自己 —— 详见
> [`docs/设计文档.md`](docs/设计文档.md) 第 8 章「风险与合规」。

**变声（RVC / DDSP-SVC 这类社区角色模型）**：它们多是「音频 → 音频」，不能直接喂文本，
所以走「先合成、再变声」的组合管线。完整步骤见
[`integrations/rvc/README.md`](integrations/rvc/README.md)：

```bash
# 一次性：装变声引擎（独立 venv）
export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk   # 本机 27.0 SDK 坏了
uv venv ~/.local/share/nekopal/rvc-venv --python 3.11
uv pip install --python ~/.local/share/nekopal/rvc-venv/bin/python \
    infer-rvc-python "setuptools<81" "transformers<5"

# 常驻变声服务（强烈推荐：单句 5.5s → 1.67s）
~/.local/share/nekopal/rvc-venv/bin/python integrations/rvc/serve.py --port 7899
```

**默认音色**：开箱只用**明确可用的来源**，无需任何额外素材——
默认是 **Edge-TTS「晓晓」**（联网、带词级时间戳），并配了**离线兜底**：断网或后端不可用时**逐句自动回退**到系统 `say`，不会没声音。
另可选 **VOICEVOX**（日语，商用可）。想用别的音色（包括自己录的声音）？把模型放进 `voices/` 即可，**项目不内置、不下载任何第三方模型**。

（想改默认音色：`NEKOPAL_DEFAULT_VOICE=edge:zh-CN-YunxiNeural uv run nekopal serve`）

**其他程序怎么推文本进来**（不需要任何系统权限）：

```bash
curl -X POST http://127.0.0.1:8787/api/read \
  -H 'Content-Type: application/json' \
  -d '{"text":"要朗读的一整块文字","voice":"say:auto"}'
# → {"task_id":"tsk_xxxxxxxx","segments":3,"sentences":3,"start_index":0}
# 再订阅 SSE 拿音频：GET /api/read/{task_id}/events
```

---

## 与其他工具集成

| 方式 | 适用场景 | 状态 |
| --- | --- | --- |
| 跟读窗粘贴 | 手动，最直接 | ✅ |
| `POST /api/read` | 任何程序推送整块文本 | ✅ |
| `scripts/push.py` | 命令行 / 编辑器插件 / 定时任务 | ✅ |
| **opencode 插件** | opencode 回答完**自动**朗读 | ✅ 已提供 |

### opencode：回答完自动朗读

```bash
uv run nekopal serve        # 先起服务
```

在 `~/.config/opencode/opencode.jsonc` 里加载插件，然后重启 opencode：

```jsonc
{
  "plugins": ["~/NekoPal/integrations/opencode-plugin"]
}
```
（路径按你 clone 的实际位置改）

它监听 `session.idle`（会话完成），取回最后一条足够长的助手消息推给跟随窗。
环境变量 `NEKOPAL_DEBUG=1` 可打印每个事件类型用于排查。
详见 [`integrations/opencode-plugin/README.md`](integrations/opencode-plugin/README.md)。

### 命令行推送

```bash
echo "一整块文字" | uv run python scripts/push.py
uv run python scripts/push.py --file 讲义.md --voice edge:zh-CN-XiaoxiaoNeural --follow
```

---

## 架构一图

```
┌──── 抓取层 capture ────┐   ┌────────── 后端 server ──────────┐   ┌── 跟读窗 web ──┐
│  整块抓取 (http_in)     │   │  清洗 → 分句 → 预合成队列        │   │  文本渲染       │
│  opencode 插件          │──►│  音频缓存 (内容寻址)             │──►│  高亮跟随       │
│  本地文件 (md/doc)      │HTTP│  TTS 后端注册表 (可插拔)         │SSE│  自动滚动       │
│  选区 (可选)            │   │  导出 / 进度书签                 │   │  播放控制       │
└────────────────────────┘   └─────────────────────────────────┘   └───────┬────────┘
                              say │ edge-tts │ gpt-sovits                    │ 状态同步
                                                                     ┌──────▼────────┐
                                                                     │ 角色层 avatar │
                                                                     │ Live2D / 口型 │
                                                                     └───────────────┘
```

各层**只通过一份接口契约通信**，因此每一层都可以单独替换或扩展（详见 [`docs/设计文档.md`](docs/设计文档.md) 第 6 章「扩展点」）。

---

## 目录导航

| 路径 | 内容 |
| --- | --- |
| [`docs/设计文档.md`](docs/设计文档.md) | **主文档**：目标、架构、接口契约、核心机制、扩展点、里程碑 |
| [`docs/开发规范.md`](docs/开发规范.md) | 目录结构、代码风格、Git 约定、测试约定 |
| [`voices/`](voices/README.md) | 音色包目录（只放规范与示例，**不含素材**） |
| [`characters/`](characters/README.md) | 角色包目录（Live2D 模型与音色的组合，**不含素材**） |
| `src/nekopal/` | 后端源码：`core/`（领域逻辑）、`tts/`、`capture/`、`avatar/`、`server/` |
| `web/` | 跟读窗前端，原生 HTML/CSS/JS，**无构建步骤** |
| `tests/` | 离线单元测试（99 个，`pytest`；**全部离线**，不联网） |
| `scripts/smoke.py` | 端到端冒烟测试：POST → SSE → 取音频 |
| `scripts/push.py` | 把整块文字推给运行中的 NekoPal（管道 / 文件） |
| `integrations/opencode-plugin/` | opencode V2 插件：回答完自动朗读 |
| `docs/images/` | README 用的截图 |

---

## 路线图

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| **Phase 0** | 打通 Edge-TTS，拿到音频 + 词级时间戳；实测本机速度 | ✅ 完成（`say` RTF≈0.13，首段 0.53s） |
| **Phase 1 · MVP** | 整块抓取 + 后端（分句/队列/缓存/后端抽象）+ 跟读窗（高亮/滚动/语速） | ✅ 完成（`macos_ax` 划词除外） |
| **Phase 2 · 集成** | opencode 插件自动朗读 + CLI 推送 + **导出音频**（wav/m4a） | ✅ 完成 |
| **P1 · 外壳形态** | **pywebview 原生窗口**（自动朗读免点击） | ✅ 完成 |
| **P1.5 · 变声** | RVC 组合后端 + **常驻变声服务**（单句 5.5s → 1.67s）+ 词级高亮透传 | ✅ 完成 |
| **P2 · Follow 边写边读** | 助手一边写、一边念（`SentenceStream` + `POST /api/read/{id}/append`） | ✅ **已完成** |
| **Phase 2 · 剩余** | **文件模式**（读 `.md`）· **进度书签**（需持久层） | 🔜 **下一步** |
| **Phase 3 · 角色化** | **角色层（Live2D + 动作 + 表情 + 物理 + 眨眼 + 口型同步）** | ✅ **完成**（用官方 Cubism 5 框架 ✗） |
| **Phase 4 · 桌宠** | **原生 NSPanel 外壳**（浮在全屏之上 / 透明 / 精确命中 / 右键菜单 / 换装 / 缩放） | ✅ **完成** |
| **Phase 5 · 集成** | 主窗「形象」下拉 + **点开 App 一起开** + 两个窗口双向同步 | ✅ **完成** |
| **Phase 6 · AI 角色** | **点击随机说话**（动作+表情+语音 ✗）· **大模型现编台词**（角色性格 + 私人信息 ✗） | ✅ **完成** |
| **Phase 6 · 剩余** | 动作菜单归类 · **贴合人物**（窗口按角色比例 ✗）· 文件模式 · 进度书签 · 跨应用划词 | 🔜 **下一步** |

**建议顺序**：`文件模式`（把「读我的回答」扩展到「读你的讲义」，最独立、最好验证）→
`进度书签 + 持久层`（顺手还掉一笔技术债）。

详细验收标准见 [`docs/设计文档.md`](docs/设计文档.md) 第 7 章。

---

### 全局快捷键：不切窗口就能暂停 / 继续 🎧

**为什么需要它**：NekoPal 通常在**另一个窗口**里 —— opencode 全屏时切过去很麻烦。
所以控制入口**不依赖窗口聚焦**：命令发给后端，后端再广播给跟读窗。

> 🐱 **更省事的办法：置顶小条** —— `nekopal pet`
> 一个**透明、无边框、全局置顶**的小卡片，点一下 `⏯` 就暂停/继续。
> 它一直在最上面，所以**不用切窗口、也不用记快捷键**。
> （这条就是「桌宠」的第一步；等有 Live2D 模型后，同一个窗口会换成角色本体。）

```bash
nekopal control toggle     # 暂停 / 继续（来回切）
nekopal control pause      # 只暂停
nekopal control resume     # 只继续
nekopal control stop       # 停止
nekopal control next|prev  # 下一句 / 上一句
```

任何时候都能用，**跟当前焦点在哪个窗口无关** ✅

#### 怎么绑到键盘（三选一）

**① 快捷指令（Shortcuts.app）—— 推荐，系统自带**

1. 打开「快捷指令」→ 新建
2. 加操作「**运行 Shell 脚本**」，脚本填**绝对路径**：
   ```
   ~/Documents/NekoPal/.venv/bin/nekopal control toggle
   ```
   （快捷键调用时 PATH 很干净，所以**必须写绝对路径**）
3. 在快捷指令的详情面板里点「**添加键盘快捷键**」→ 按一个组合（如 `⌃⌥⌘P`）

**② Automator 快速操作 + 系统设置**

1. Automator → 新建「快速操作」→ 加「运行 Shell 脚本」→ 同上填命令 → 存为「NekoPal 暂停继续」
2. 系统设置 → 键盘 → 键盘快捷键 → **服务** → 找到它 → 指定快捷键

**③ 键盘 / 鼠标驱动软件**（Karabiner、BetterTouchTool 等）→ 直接绑 `scripts/toggle.command`

#### 跟读窗自己聚焦时

**按空格** = 暂停 / 继续 ✅（不用去点按钮）

> ⚠️ 后退一步的兜底：`nekopal control` 需要 NekoPal 在跑。
> 如果它说「当前没有跟读窗连着」，说明窗口没开 —— 双击 `NekoPal.app` 即可。

---

## 设计原则

1. **离线优先，联网可选**：默认后端必须能离线工作，联网后端是"可选升级"。
2. **零依赖可用**：不装 PyTorch 也能跑；高质量音色与角色按需升级。
3. **接口先行**：抓取源、TTS 后端、清洗规则、角色层、播放参数全部通过抽象接口接入，主流程不写死。
4. **能力协商**：后端/角色声明自己支持什么（词级时间戳？变速？口型？），前端据此降级而不是硬编码假设。
5. **只做接口，不做分发**：项目提供的是**能力**，不是**内容**。第三方音色/角色模型一律由用户自备——不内置、不下载、不提供下载入口。
6. **默认即合法**：开箱可用的音色/角色全部来自**明确可用的来源**（系统音色、Edge-TTS 官方音色、VOICEVOX），无需任何额外素材即可完整使用。
7. **授权透明**：素材能否商用、能否再分发，在配置与界面中显式可见，判断权交给用户。
8. **朗读是核心，角色是加分**：即使不要角色、不要桌宠，它也必须是一个好用的朗读器。

---

## 许可证

**MIT**（见 [`LICENSE`](LICENSE)）。注意：**音色 / 角色素材另有其权属**，不在本许可范围内（见「仓库零素材」）。

## 致谢

本项目的语音与角色能力建立在以下开源工作之上：

- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) —— 少样本声音克隆
- [edge-tts](https://github.com/rany2/edge-tts) —— Edge 朗读接口的 Python 封装（非官方）
- [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) / [AIRI](https://github.com/moeru-ai/airi) —— Live2D 桌面伴侣的先行者
- [Live2D Cubism](https://www.live2d.com/) / [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) —— Live2D 渲染
- [Kokoro](https://github.com/hexgrad/kokoro) / [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) —— 轻量离线 TTS 备选
