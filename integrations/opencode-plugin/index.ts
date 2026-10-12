/**
 * NekoPal × opencode —— 自动把助手的回答推给 NekoPal 朗读（OpenCode V2 插件）。
 *
 * 触发链（实测采集 2026-10-13）：
 *   session.inbox.enqueued → session.execution.started
 *     → session.step.* / session.tool.* / session.text.* …
 *   session.execution.succeeded      ← 「一轮成功收尾」，本插件在此动作
 *
 * ⚠️ 官方文档示例里写的 `session.idle` 在这版 opencode 中**不存在**；
 *    实测存在的是 `session.execution.succeeded`。因此触发器同时匹配两者，
 *    并在收到**未知事件类型**时上报类型与负载样本，便于日后适配。
 *
 * 设计取舍：
 *   - **不 import `@opencode/plugin`**：V2 插件即「默认导出带 id 与 setup 的对象」，
 *     少一个依赖就少一处加载失败的可能。
 *   - **不猜事件负载形状**：负载字段在 OpenAPI 中不透明，因此递归定位 sessionID，
 *     并把定位失败时的负载样本上报到 `/api/activity`（而不是让人去翻日志）。
 *   - **只发往本机**：不经过任何第三方服务。
 */

const BASE = process.env.NEKOPAL_BASE ?? "http://127.0.0.1:8787";
const DEBUG = process.env.NEKOPAL_DEBUG === "1";
/** 短于此长度的回答不朗读（避免"好的""已完成"这类噪音） */
const MIN_CHARS = Number(process.env.NEKOPAL_MIN_CHARS ?? 60);
const MAX_TRACKED = 32;
const MAX_REPORTED_TYPES = 60;
const DUMP_LIMIT = 1200;

type AnyRecord = Record<string, any>;

/** 一轮结束的触发事件：实测名 + 文档里的旧名（兼容）。 */
const TRIGGER = /(^|\.)(execution\.succeeded|idle)$/;

function dump(value: unknown, limit = DUMP_LIMIT): string {
  try {
    return JSON.stringify(value ?? null).slice(0, limit);
  } catch {
    return "<unserializable>";
  }
}

/** 递归查找会话 ID：先看键名，再看 `{session:{id}}`，最后看形如 `ses_xxx` 的字符串。 */
function findSessionID(value: unknown, depth = 0, budget = { visits: 0 }): string | undefined {
  if (value == null || depth > 7 || budget.visits > 6000) return undefined;
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

  // 1) 直接键名：sessionID / session_id / sessionId
  for (const key of Object.keys(record)) {
    if (/^session_?id$/i.test(key) && typeof record[key] === "string") {
      return record[key] as string;
    }
  }
  // 2) 嵌套形态：{ session: { id: "..." } }
  const nested = record.session;
  if (nested && typeof nested === "object" && typeof (nested as AnyRecord).id === "string") {
    return (nested as AnyRecord).id as string;
  }
  // 3) 深度搜索
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
 * 加载探针：模块一被求值就上报一次。
 *
 * 用来区分三种情况——「插件没被加载」「加载了但 setup 没跑」「setup 跑了但收不到事件」。
 */
void report("module-loaded", { base: BASE, minChars: MIN_CHARS });

export default {
  id: "nekopal",

  setup(ctx: AnyRecord) {
    const controller = new AbortController();
    const seen: string[] = [];
    const reportedTypes = new Set<string>();

    void report("plugin-ready", { base: BASE, minChars: MIN_CHARS });

    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        const type = (event as AnyRecord)?.type;

        // 事件类型词表 + 负载样本：opencode 改事件结构时靠它快速适配
        if (typeof type === "string" && !reportedTypes.has(type) && reportedTypes.size < MAX_REPORTED_TYPES) {
          reportedTypes.add(type);
          void report("event-type", { type, sample: dump(event, 400) });
        }
        if (DEBUG) console.log("[nekopal] event:", type);

        if (typeof type !== "string" || !TRIGGER.test(type)) continue;

        const payload = (event as AnyRecord).data ?? (event as AnyRecord).properties ?? event;
        await report("trigger", { type, payload: dump(payload) });

        const sessionID = findSessionID(event);
        if (!sessionID) {
          await report("no-session-id", { type, dump: dump(event) });
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
          console.warn("[nekopal] 处理完成事件失败:", String(error));
          await report("error", { sessionID, message: String(error).slice(0, 300) });
        }
      }
    })();

    return () => controller.abort();
  },
};
