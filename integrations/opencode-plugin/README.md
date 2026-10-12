# NekoPal × opencode 插件

让 opencode **每回答完一次，就自动把整条回答推给 NekoPal 朗读** —— 不用复制粘贴。

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
  "plugins": ["/Users/yuwenyan/Documents/NekoPal/integrations/opencode-plugin"]
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

日志位置：`~/.local/share/opencode/log/opencode.log`

以 `NEKOPAL_DEBUG=1` 启动 opencode，然后搜索 `[nekopal]`：

| 现象 | 含义 | 怎么办 |
| --- | --- | --- |
| 完全搜不到 `[nekopal]` | 插件没被加载 | 检查 `plugins` 配置路径是否正确、opencode 是否重启过 |
| 有 `event: session.idle`，但没有「已推送」 | 定位不到 `sessionID`，或没有足够长的助手消息 | 把 `[nekopal] 未能从事件中定位 sessionID` 附近的日志发给维护者 |
| 有「已推送」但 NekoPal 没反应 | 服务没起，或地址不对 | `curl $NEKOPAL_BASE/api/health` 确认 |
| 推送失败 HTTP 404/501 | NekoPal 版本不匹配 | 确认 NekoPal 是最新的 |

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
- `session.idle` 事件负载的具体字段在 OpenAPI 中是不透明的，插件用**递归搜索**定位 `sessionID`。
  如果 opencode 改了事件结构，用 `NEKOPAL_DEBUG=1` 能立刻看出来，改 `findSessionID` 即可。
- 只朗读**最后一条**够长的助手消息；如果你想要"把整轮对话都读"，这属于 Phase 2 的 Follow 模式。
