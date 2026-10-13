# RVC 变声接入（NekoPal × infer_rvc_python）

把 RVC 这类「音频 → 音频」的变声模型，接成 NekoPal 能用的音色。

```
文本 ──edge/say 合成──► WAV ──convert_cli.py──► 目标音色 ──► 播放 / 导出
                                    ↑
                            infer_rvc_python（独立 venv）
```

**本项目不提供任何模型。** 模型请自行准备（自用），并如实填写音色包里的 `license`。

---

## 一、安装变声引擎（**已在本机完成**，此处留档）

```bash
# ⚠️ 关键：本机默认 SDK（MacOSX27.0）坏掉了，必须指到 26
export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk

# 变声引擎跑独立 venv（不和 NekoPal 共用；Python 3.12 装不上，必须 3.11）
uv venv ~/.local/share/nekopal/rvc-venv --python 3.11
uv pip install --python ~/.local/share/nekopal/rvc-venv/bin/python \
    infer-rvc-python "setuptools<81" "transformers<5"
```

**为什么是这三个坑**（都不是 RVC 的问题）：

| 坑 | 症状 | 修法 |
| --- | --- | --- |
| 本机 SDK 坏了 | `ld: tapi error: malformed file` | `export SDKROOT=.../MacOSX26.sdk` |
| Python 3.12 | `numpy 1.25.2` 编译失败（3.12 删了 `distutils`） | 用 **3.11** |
| setuptools 84 | `No module named 'pkg_resources'` | `setuptools<81` |
| transformers 5 | `Could not import module 'HubertModel'` | `transformers<5` |

> 不用装 ffmpeg：我们的输入永远是 WAV，`convert_cli.py` 用 `soundfile` 直接读数组喂给它。

---

## 二、告诉 NekoPal 怎么调用

```bash
export NEKOPAL_RVC_COMMAND='~/.local/share/nekopal/rvc-venv/bin/python \
  ~/NekoPal/integrations/rvc/convert_cli.py \
  {input} {output} --model {model} --index {index} --pitch {pitch} --quiet'
```

> 环境变量用的是 shlex 解析，所以**不能在值里用 `~`**（它不会被展开）。
> 稳妥写法是写全路径，或把它放进 shell 启动脚本（`~/.zshrc`）里再 export。

自检：

```bash
nekopal convert show                      # 看当前命令
nekopal convert test --pack my-converted  # 合成 → 变声 → 报告
```

---

## 三、建音色包

```bash
nekopal packs init voice my-converted
# 然后把你的模型拷进去
cp ~/Downloads/voice.pth   ~/.config/nekopal/voices/my-converted/
cp ~/Downloads/voice.index ~/.config/nekopal/voices/my-converted/   # 可选
```

编辑 `~/.config/nekopal/voices/my-converted/voice.yaml`：

```yaml
id: my-converted
name: "我的变声音色"
backend: rvc
lang: ["zh", "ja"]
tags: ["community"]
license: unofficial             # 未获公开授权的模型如实填写
commercial_ok: false
redistributable: false
rvc:
  base_voice: edge:zh-CN-XiaoxiaoNeural
  model: voice.pth
  index: voice.index             # 没有就删掉这行
```

最后：

```bash
nekopal packs list       # 应该看到 rvc:my-converted，没有「已跳过」
nekopal convert test --pack my-converted
```

---

## 四、首次运行的额外下载

`infer_rvc_python` 第一次跑会从 HuggingFace 拉 **HuBERT 基础模型**（`r3gm/hubert_base`，几百 MB）。
下载一次之后会缓存。默认音高算法是 `pm`（praat-parselmouth），**不需要额外下载 RMVPE**。

---

## 五、排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| **段错误 / 进程静默退出（无报错）** | **faiss 的 OpenMP 线程创建在 macOS arm64 上崩溃**（`libomp __kmp_create_worker` → SIGSEGV） | ✅ 已内置修复：脚本里设了 `OMP_NUM_THREADS=1`（**只设 `KMP_DUPLICATE_LIB_OK` 不够**） |
| `变声失败：模型不存在` | 路径不对 | 检查 `voice.yaml` 里的 `model`，模型要放在音色包目录里 |
| `Missing 'config' dictionary in checkpoint` | 用了**训练用基础模型**（如 `pretrained_v2/f0G40k.pth`），不是推理模型 | 必须用**训练好的**模型（含 `config` 字典） |
| `import infer_rvc_python 失败` | 用错解释器 | 必须是 `~/.local/share/nekopal/rvc-venv/bin/python` |
| `No module named 'pkg_resources'` | setuptools 太新 | 装 `"setuptools<81"` |
| `Could not import module 'HubertModel'` | transformers 太新 | 装 `"transformers<5"` |
| 卡很久没输出 | 首次在下载 HuBERT | 耐心等；或 `--quiet` 看不到进度 |
| 输出有电音/杂音 | `pitch_lvl` 不对 | 男声→女声试试 `--pitch 12`，反之 `-12` |

> 📌 **排查心得**：进程**静默退出、没有 Python 报错**时，去
> `~/Library/Logs/DiagnosticReports/*.ips` 看崩溃报告 ——
> 它能直接告诉你崩在哪个库的哪个函数（我们就是这样定位到 `libomp` 的）。
| 速度很慢 | CPU 推理 | 正常；NekoPal 的预合成队列会掩盖一部分 |

---

## 六、性能提示

**实测（Apple M5，VCTK 女声模型）**：

| 方式 | 单句（4.27s 音频） | RTF |
| --- | --- | --- |
| **每句新起进程**（本地推理） | **约 5.5 秒** | — |
| **常驻服务**（推荐） | **1.67 秒** | **0.39** ✅ |

**瓶颈不在推理（RTF 0.39，比实时快 2.5 倍），而在"每句话都新起一个进程"**：
python + torch/faiss 导入 + HuBERT 加载 ≈ **3.5 秒固定开销**。

## 七、常驻变声服务（强烈推荐开启）

```bash
# 启动（跑在装了 RVC 的那个 venv 里）
~/.local/share/nekopal/rvc-venv/bin/python integrations/rvc/serve.py --port 7899

# 让 NekoPal 走它（写进 ~/.zshrc 才持久）
export NEKOPAL_RVC_ENDPOINT=http://127.0.0.1:7899
```

- 模型只加载一次，之后每句只走一次 HTTP → **单句 5.5s → 1.67s**
- `convert_cli.py` 会**自动优先用服务**；服务没起就**自动退回本地推理**（只是慢）
- 健康检查：`curl http://127.0.0.1:7899/health`
- `--endpoint-only` 可以禁止回退（调试用）

> ⚠️ 换音色时（从一个变声音色切到另一个）服务会**重新加载模型**（几秒），
> 之后该音色的每一句都是 1.67s。

- 变声**不产出词级时间戳** → 高亮自动降级为**句级**（这是正常的）。
- 队列深度可调：`NEKOPAL_PREFETCH`（默认 3）；变声场景建议调到 5–6。

---

## 八、音质调参（2026-10-13 实测补充）

### 8.1 变调是「像不像」的第一杠杆 ⭐⭐⭐

RVC **保留"源"的音高**，只换音色。所以 base 比目标低 → 输出就偏低（听起来"闷、偏男"）。

**实测**：中文晓晓 → 要楽奈，**升 2 个半音**明显更贴，而且原来那种"忽然冒出来的很低的音"也没了。

### 8.2 三件「气声」旋钮 ⭐⭐

想调「有点虚 / 太实 / 想要气音」，主要靠这三个（**都已在 `convert_cli.py` 暴露**）：

| 参数 | 默认 | 怎么调 | 对应 RVC 参数 |
| --- | --- | --- | --- |
| `--breath-protection` | `0.33` | 调大 → 更保护气声段 | `consonant_breath_protection` |
| `--envelope-ratio` | `0.25` | 调大 → 更保留**源**的气声起伏（**最有效的一个**） | `envelope_ratio` |
| `--respiration-filter` | `3` | **调小** → 保留更多呼吸细节 | `respiration_median_filtering` |

用法：写进音色包的 `rvc.command`（覆盖全局命令），例如：

```yaml
rvc:
  command: [… --pitch 2 --breath-protection 0.6 --envelope-ratio 0.6 --respiration-filter 1 …]
```

### 8.3 ⚠️ `harvest` / `rmvpe` 需要 ffmpeg

这两个音高提取算法比 `pm` 准得多，但在 `infer_rvc_python` 里**只吃文件路径**，
而"按路径读音频"又要求 ffmpeg —— 本机没装，所以只有 `pm` 可用：

```
ValueError: Harvest not support from array
RuntimeError: Failed to load audio: … No such file or directory: 'ffmpeg'
```

> 我们为了让变声**不依赖 ffmpeg**，是**传数组**进去的（见本文件开头的说明）——
> 这就是拿不到 harvest 的原因。想用：`brew install ffmpeg`（一次安装，几百 MB）。

### 8.4 反直觉但重要：**模型类型决定上限** ✗

社区模型的 tags 能看出它是拿什么数据训的。例如：

```
tags: [rvc, voice-conversion, singing-voice-conversion]      ← 「歌声」模型
```

用**歌声**模型念**说话**文本，音高曲线天然不匹配
（歌声是稳定的音高，说话是连续滑动的）——
**参数只能改善、不能消除**。想要更像，得换**语音（speech）版**模型。

### 8.5 快速 A/B 的做法（这次就是这么找出最优参数的）

1. 用 `nekopal export` 生成一份 base 音频（同一个句子）
2. 固定其它参数，只改一个，各生成一份
3. 文件名里写清参数（如 `B6-全开气声-0.6+0.6+1.wav`），丢进同一个文件夹
4. 在访达里挨个听 → 挑最优 → 写进 `rvc.command`

> 💡 每份只要几秒（走常驻服务），所以一次试 6–8 组完全不心疼。

### 8.6 「软萌慵懒」怎么拆（2026-10-13 调音总结）

| 特质 | 声学上是什么 | 能不能控 |
| --- | --- | --- |
| **软** | 气声多、动态柔和 | ✅ 气声三旋钮（8.2） |
| **慵懒** | **语速慢** + 音高变化小 | ✅ `rvc.rate`（8.7）+ 选对 base（8.1） |
| **萌** | 音高偏高 **+ 共振峰偏小** | ⚠️ 音高能控，**共振峰由模型决定** ✗ |

### 8.7 `rvc.rate`：调 **base 的语速**（「慵懒」的主要旋钮）

```yaml
rvc:
  base_voice: edge:zh-CN-XiaoyiNeural
  rate: 0.85                  # ← 0.5~2.0，越小越慢
```

- 它改的是**源怎么说**（不是前端 `playbackRate` —— 那个只改播放速度，不碰语气）
- RVC 保留时间轴 → 源慢下来，**最终输出也跟着慢** ✅
- 实测：`rate 0.85` → 6.64s 变 7.78s（慢 17%）· **音高起伏 σ 基本不变**
  → 所以「语速」和「起伏」是**两个独立旋钮**，可以自由组合 ✅

### 8.8 ⚠️ 换 base 必须**对齐音高**才能公平比较

`--pitch` 是**乘性**移调（×2^(n/12)），所以它会把**音高波动也按比例放大**：

| 变调 | 音高中位 | 起伏 σ |
| --- | --- | --- |
| 0 | 241Hz | **50.3** |
| +2 | 267Hz | 56.9（+13%） |
| +4 | 301Hz | 64.0（+27%） |
| +6 | 335Hz | 71.8（+42%） |

**含义**：想要"音高贴角色"就得升调，但升调会让声音**更"有激情"** —— 这是个真实取舍。

**破局**：换一个**本来音高就合适**的 base，用**降调**到目标音高（降调会**缩小** σ）。
实测（都对齐到 ~268Hz）：

| base | 需要的变调 | 结果 σ |
| --- | --- | --- |
| 晓晓 | +2 | 57.1 |
| **晓伊（303Hz）** | **−2** | **48.3** ✅ 平 15% |
| 粤语 HiuMaan | +1 | 52.1 |
| 台湾 HsiaoChen | +6 | 64.1 ✗ |

> 📌 **快速度量**：用 `praat-parselmouth`（就在 RVC 的 venv 里）取
> `median(f0)`（音高，像不像）与 `std(f0)`（起伏，激不激情）—— 两个数就能定方向，
> 不用只靠耳朵。

### 8.9 🐛 包音色合成失败会**静默退回兜底音色**（踩过）

`nekopal export --voice rvc:rana` 曾经**导出成 say 的声音**且**毫无提示** ——
原因是导出路径**没有把音色包查询接上**，`RvcBackend` 找不到包 → 合成失败 →
`synthesize_sentence` 按"单句失败"逻辑**退回 `fallback_voice`** ✗

两个修法（都已落地）：

1. 导出路径也要 `attach_voice_packs(registry)`（见 `cli.py`）
2. **不许静默**：`nekopal export` 现在会打印「有 N 句退回了兜底音色 —— 导出的不是你要的音色」

> 💡 排查手法：**量一下导出的音高**。要楽奈 ≈285Hz，而 `say:Tingting` ≈237Hz ——
> 一眼就能看出"拿到的是不是同一个人"。
