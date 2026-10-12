/**
 * NekoPal × opencode —— 自动把助手的回答推给 NekoPal 朗读（OpenCode V2 插件）。
 *
 * 工作方式：
 *   1. 订阅 opencode 的公开事件流；
 *   2. 收到 `session.idle`（会话完成）且结果为成功时，定位 sessionID；
 *   3. 用 `ctx.session.context()` 取回会话消息，从后往前找**第一条足够长**的助手消息；
 *   4. 把该消息里 `type === "text"` 的内容块拼起来，POST 给本机 NekoPal。
 *
 * 设计取舍：
 *   - **不 import `@opencode/plugin`**：V2 插件就是「默认导出带 id 与 setup 的对象」，
 *     少一个依赖就少一处加载失败的可能。
 *   - **不猜事件负载形状**：`session.idle` 的具体字段在 OpenAPI 里是不透明的，
 *     因此用递归搜索定位 sessionID，并对找不到的情况给出可诊断的日志。
 *   - **只发往本机**：不经过任何第三方服务。
 */

const BASE = process.env.NEKOPAL_BASE ?? "http://127.0.0.1:8787";
const DEBUG = process.env.NEKOPAL_DEBUG === "1";
/** 短于此长度的回答不朗读（避免"好的""已完成"这类噪音） */
const MIN_CHARS = Number(process.env.NEKOPAL_MIN_CHARS ?? 60);
const MAX_TRACKED = 32;

type AnyRecord = Record<string, any>;

/** 递归查找会话 ID；先看键名，再看形如 `ses_xxx` 的字符串值。 */
function findSessionID(value: unknown, depth = 0, budget = { visits: 0 }): string | undefined {
  if (value == null || depth > 6 || budget.visits > 4000) return undefined;
  budget.visits += 1;

  if (typeof value === "string") {
    return /^ses[-_A-Za-z0-9]{3,}$/.test(value) ? value : undefined;
  }
  if (typeof value !== "object") return undefined;

  if (Array.isArray(value)) {
    for (const item of value) {
      const hit = findSessionID(item, depth + 1, budget);
      if (hit) return hit;
    }
    return undefined;
  }

  const record = value as AnyRecord;
  for (const key of Object.keys(record)) {
    if (/^session_?id$/i.test(key) && typeof record[key] === "string") {
      return record[key] as string;
    }
  }
  for (const key of Object.keys(record)) {
    const hit = findSessionID(record[key], depth + 1, budget);
    if (hit) return hit;
  }
  return undefined;
}

/** 从一条助手消息里取出纯文本部分。 */
function assistantText(message: AnyRecord): string {
  if (!message || message.type !== "assistant" || !Array.isArray(message.content)) return "";
  return message.content
    .filter((part: AnyRecord) => part && part.type === "text" && typeof part.text === "string")
    .map((part: AnyRecord) => part.text as string)
    .join("\n\n")
    .trim();
}

/** 从后往前找第一条足够长的助手消息（跳过工具调用的中间步骤）。 */
function latestReadableAnswer(messages: AnyRecord[]): { id: string; text: string } | undefined {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const text = assistantText(messages[i]);
    if (text.length >= MIN_CHARS) {
      return { id: String(messages[i].id ?? i), text };
    }
  }
  return undefined;
}

async function push(text: string, sessionID: string): Promise<void> {
  const response = await fetch(`${BASE}/api/read`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      source: { kind: "block", app: "opencode", session_id: sessionID },
    }),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    console.warn(`[nekopal] 推送失败 HTTP ${response.status} ${detail.slice(0, 200)}`);
    await report("push-failed", { sessionID, status: response.status, detail: detail.slice(0, 200) });
    return;
  }
  if (DEBUG) {
    console.log(`[nekopal] 已推送 ${text.length} 字（session ${sessionID}）`);
  }
  await report("pushed", { sessionID, chars: text.length });
}

/**
 * 向 NekoPal 汇报一次活动。
 *
 * 插件的成功路径是静默的，出了问题很难查 —— 所以把关键节点记到 NekoPal 的
 * `/api/activity`，用 `GET /api/activity` 就能看到「到底走到哪一步」。
 * 汇报失败绝不影响朗读主流程。
 */
async function report(event: string, detail?: unknown): Promise<void> {
  try {
    await fetch(`${BASE}/api/activity`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, source: "opencode-plugin", detail }),
    });
  } catch {
    /* 忽略：NekoPal 没起也不该影响 opencode */
  }
}

export default {
  id: "nekopal",

  setup(ctx: AnyRecord) {
    const controller = new AbortController();
    const seen: string[] = [];

    void report("plugin-ready", { base: BASE, minChars: MIN_CHARS });

    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        const type = (event as AnyRecord)?.type;
        if (DEBUG) console.log("[nekopal] event:", type);
        if (type !== "session.idle") continue;

        const payload = (event as AnyRecord).data ?? (event as AnyRecord).properties ?? {};
        await report("idle", { outcome: payload.outcome ?? null });
        // 只在成功的收尾动作上朗读；失败/中断不读
        if (payload.outcome && payload.outcome !== "succeeded") continue;

        const sessionID = findSessionID(event);
        if (!sessionID) {
          const dump = JSON.stringify(event ?? {}).slice(0, 600);
          if (DEBUG) console.warn("[nekopal] 未能从事件中定位 sessionID：", dump);
          await report("no-session-id", { dump });
          continue;
        }

        try {
          const messages = ((await ctx.session.context({ sessionID })) ?? []) as AnyRecord[];
          const answer = latestReadableAnswer(messages);
          if (!answer) {
            await report("no-answer", { sessionID, messages: messages.length, minChars: MIN_CHARS });
            continue;
          }
          if (seen.includes(answer.id)) continue; // 同一轮不重复朗读

          seen.push(answer.id);
          if (seen.length > MAX_TRACKED) seen.shift();

          await push(answer.text, sessionID);
        } catch (error) {
          console.warn("[nekopal] 处理 session.idle 失败:", String(error));
          await report("error", { sessionID, message: String(error).slice(0, 300) });
        }
      }
    })();

    return () => controller.abort();
  },
};
