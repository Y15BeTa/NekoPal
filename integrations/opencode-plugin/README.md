# NekoPal × opencode 插件

让 opencode **每回答完一次，就自动把整条回答推给 NekoPal 朗读** —— 不用复制粘贴。

> ✅ **状态：已实测闭环**（2026-10-13）。
> 完整链路：`session.execution.succeeded` → 取回最后一条够长的助手回答 → `POST /api/read`
> → 服务端广播 `task-created` → 跟读窗自动接上 → 词级高亮 + 自动滚动播放。

## 使用前提（两个，一次性）

1. **跟读窗要保持打开**（可以是后台标签页）：<http://127.0.0.1:8787/>
2. **点一次页面上的「启用自动朗读」**：浏览器规定媒体播放必须发生在用户手势之后，
   而插件推送是异步到达的，永远不可能落在手势里 —— 所以必须提前点一次。

> 💡 这两条限制会在「原生外壳」（pywebview / Electron）接上后消失，
> 因为那时可以直接用 `afplay` 播放，不经过浏览器。

## 启用

1. 先启动 NekoPal：

```bash
cd ~/Documents/NekoPal
uv run nekopal serve            # 默认 http://127.0.0.1:8787
```

2. 在 `~/.config/opencode/opencode.jsonc` 里加载本插件（路径按你的实际位置改）：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["~/Documents/NekoPal/integrations/opencode-plugin"]
}
```

3. **重启 opencode**。

之后每次会话结束，跟读窗就会自动开始念。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `NEKOPAL_BASE` | `http://127.0.0.1:8787` | NekoPal 服务地址 |
| `NEKOPAL_MIN_CHARS` | `60` | 短于此长度的回答不朗读（过滤"好的""已完成"这类噪音） |
| `NEKOPAL_DEBUG` | `0` | 设为 `1` 时把**每个事件类型**打到日志，用于排查 |

## 排查

### 一、看 NekoPal 的活动记录（最快）

插件会把关键节点记到 NekoPal，直接查就行：

```bash
curl -s http://127.0.0.1:8787/api/activity | python3 -m json.tool
```

| `event` | 含义 | 下一步 |
| --- | --- | --- |
| `plugin-ready` | 插件已加载并开始订阅事件 | 正常 |
| `idle` | 收到会话完成事件 | 正常；看后面还有没有别的记录 |
| `no-answer` | 会话里没有足够长的助手消息 | 回答太短（默认 <60 字不读），或 `NEKOPAL_MIN_CHARS` 太大 |
| `no-session-id` | **定位不到 sessionID** | 事件结构变了，把 `detail.dump` 发给维护者 |
| `pushed` | **推送成功** | 搞定 |
| `push-failed` | NekoPal 拒绝了请求 | 看 `detail.status`，通常是服务没起或版本不匹配 |
| `error` | 插件内部报错 | 看 `detail.message` |

### 二、看 opencode 日志

日志位置：`~/.local/share/opencode/log/opencode.log`

| 现象 | 含义 | 怎么办 |
| --- | --- | --- |
| 搜不到 `loading plugin` | 插件没被加载 | 检查 `plugins` 配置路径、重启 opencode |
| 有 `loading plugin` 但 `/api/activity` 是空的 | `setup` 没跑或订阅没建立 | 把日志给我 |
| 推送失败 HTTP 501/404 | NekoPal 版本不匹配 | 更新 NekoPal |

> 💡 NekoPal 会自动**热重载**插件（它监听插件目录），改完 `index.ts` 通常不用重启 opencode。

## 实现说明

- 监听 **`session.idle`**（会话完成）事件，仅在 `outcome === "succeeded"` 时动作；失败/中断不朗读。
- 通过 `ctx.session.context({ sessionID })` 取回会话消息，**从后往前找第一条足够长的助手消息**，
  把其中 `type === "text"` 的内容块拼起来（自动跳过工具调用、推理等中间步骤）。
- 按消息 id 去重，同一轮不会重复朗读。
- **不 import `@opencode/plugin`**：V2 插件即「默认导出带 `id` 与 `setup` 的对象」，
  少一个依赖就少一处加载失败的可能。
- **数据不出本机**：只 POST 到你自己跑的 NekoPal。

## 已知限制

- 需要在 opencode **V2** 上使用（V1 插件实现不能在 V2 运行）。
- **事件名与官方文档示例不一致**：文档写的是 `session.idle`，实测这版 opencode **没有**该事件，
  真正的一轮收尾信号是 **`session.execution.succeeded`**。插件用 `/(^|\.)(execution\.succeeded|idle)$/`
  同时匹配两者，改名也能活。
- **插件会被加载成多个实例**（按 location 各一份），同一轮会推送多次 —— 服务端已按
  `session_id + 文本哈希` 去重，无需处理。
- 只朗读**最后一条**够长的助手消息；"把整轮对话都读"属于 Phase 2 的 Follow 模式。
- 同一条消息 id 只在**单个实例内**去重（跨实例靠服务端）。

## 附：opencode V2 实际事件类型（实测采集）

插件会把收到的每种事件类型上报一次（去重、上限 60 种），用
`curl -s http://127.0.0.1:8787/api/activity` 就能看到全貌。

在一轮对话**进行中**观测到的类型（2026-10-13 实测）：

| 分类 | 事件类型 |
| --- | --- |
| 步骤 | `session.step.started`、`session.step.streamed` |
| 推理 | `session.reasoning.started`、`session.reasoning.delta`、`session.reasoning.ended` |
| 工具 | `session.tool.called`、`session.tool.progress`、`session.tool.input.started`、`session.tool.input.ended` |
| 系统 | `shell.created`、`session.permissions`、`rpc.experimental.browser.control` |
| 注册表变更 | `plugin.updated`、`agent.updated`、`command.updated`、`model.updated`、`provider.updated`、`integration.updated`、`reference.updated`、`skill.updated`、`websearch.updated` |

> ⚠️ 注意：**没有** `session.step.ended` 这类"步骤结束"事件可用 —— 所以不能靠
> `*.ended` 判断一轮结束（推理和工具输入都会以 `.ended` 收尾）。
> 「一轮完成」的信号是 **`session.idle`**（见官方插件迁移文档的示例代码），
> 它只在整轮收尾时发出，因此不会出现在上表里。
>
> 插件对它的匹配写成了 `/(^|\.)idle$/`，以便在 opencode 改名时仍能工作。
