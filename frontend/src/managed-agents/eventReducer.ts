import type {
  ManagedAgentEvent,
  ManagedContentBlock,
  ManagedSessionStatus,
} from "../adk/managedAgents";

export type ManagedConversationEntry =
  | {
      key: string;
      kind: "message";
      role: "user" | "assistant";
      text: string;
      streaming?: boolean;
    }
  | { key: string; kind: "thinking"; text: string; streaming?: boolean }
  | { key: string; kind: "tool"; name: string; state: "running" | "completed" }
  | { key: string; kind: "error"; text: string };

export interface ManagedEventState {
  events: ManagedAgentEvent[];
  status: ManagedSessionStatus | "failed";
  lastSequence?: number;
  lastEventId?: string;
}

function normalizePreviewEvent(
  event: ManagedAgentEvent,
): ManagedAgentEvent | undefined {
  if (event.type === "event_start") {
    const preview = event.event as
      | { type?: string; id?: string }
      | undefined;
    if (preview?.type === "agent.message" && preview.id) {
      return { type: "agent.message_stream_start", message_id: preview.id };
    }
    if (preview?.type === "agent.thinking" && preview.id) {
      return { type: "agent.thinking_stream_start", thinking_id: preview.id };
    }
  }
  if (event.type === "event_delta" && event.event_id) {
    const delta = event.delta as
      | { type?: string; content?: ManagedContentBlock }
      | undefined;
    if (
      delta?.type === "content_delta" &&
      delta.content?.type === "text" &&
      typeof delta.content.text === "string"
    ) {
      return {
        type: "agent.message_chunk",
        message_id: event.event_id,
        delta: delta.content.text,
      };
    }
  }
  return event;
}

export function emptyManagedEventState(
  status: ManagedSessionStatus = "pending",
): ManagedEventState {
  return { events: [], status };
}

function textContent(content: ManagedContentBlock[] | string | undefined): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (block): block is ManagedContentBlock & { text: string } =>
        block.type === "text" && typeof block.text === "string",
    )
    .map((block) => block.text)
    .join("");
}

function eventKey(event: ManagedAgentEvent): string {
  if (typeof event.seq === "number") return "seq:" + event.seq;
  if (event.id) return "id:" + event.id;
  return [
    event.type,
    event.processed_at ?? "",
    event.message_id ?? "",
    event.thinking_id ?? "",
    event.tool_use_id ?? "",
    event.delta ?? "",
  ].join(":");
}

function eventTime(event: ManagedAgentEvent): number {
  const time = event.processed_at ? Date.parse(event.processed_at) : Number.NaN;
  return Number.isFinite(time) ? time : 0;
}

function orderedEvents(events: ManagedAgentEvent[]): ManagedAgentEvent[] {
  return events
    .map((event, index) => ({ event, index }))
    .sort((left, right) => {
      if (typeof left.event.seq === "number" && typeof right.event.seq === "number") {
        return left.event.seq - right.event.seq;
      }
      const timeDifference = eventTime(left.event) - eventTime(right.event);
      return timeDifference || left.index - right.index;
    })
    .map(({ event }) => event);
}

function derivedStatus(
  events: ManagedAgentEvent[],
  fallback: ManagedEventState["status"],
): ManagedEventState["status"] {
  let status = fallback;
  for (const event of events) {
    if (event.type === "user.message" || event.type === "session.status_running") {
      status = "running";
    } else if (event.type === "session.status_idle") {
      status = "idle";
    } else if (event.type === "session.status_rescheduled") {
      status = "rescheduling";
    } else if (event.type === "session.status_terminated") {
      status = "terminated";
    } else if (event.type === "session.error") {
      status = "failed";
    }
  }
  return status;
}

export function mergeManagedEvents(
  state: ManagedEventState,
  incoming: readonly ManagedAgentEvent[],
): ManagedEventState {
  const byKey = new Map(state.events.map((event) => [eventKey(event), event]));
  for (const raw of incoming) {
    const event = normalizePreviewEvent(raw);
    if (event) byKey.set(eventKey(event), event);
  }
  const events = orderedEvents([...byKey.values()]);
  let lastSequence = state.lastSequence;
  let lastEventId = state.lastEventId;
  for (const event of events) {
    if (typeof event.seq === "number" && (lastSequence === undefined || event.seq > lastSequence)) {
      lastSequence = event.seq;
      lastEventId = String(event.seq);
    }
  }
  return {
    events,
    status: derivedStatus(events, state.status),
    lastSequence,
    lastEventId,
  };
}

function safeEventError(event: ManagedAgentEvent): string {
  const raw = typeof event.error === "string" ? event.error : event.error?.message;
  if (!raw) return "Agent 执行失败，请检查 Worker 和模型服务后重试。";
  return raw
    .replace(/Bearer\s+[A-Za-z0-9._~+\/-]+/gi, "Bearer <redacted>")
    .replace(
      /(["']?(?:api[_-]?key|token|secret|password|authorization)["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi,
      "$1<redacted>",
    )
    .slice(0, 500);
}

export function managedConversationEntries(
  events: readonly ManagedAgentEvent[],
): ManagedConversationEntry[] {
  const entries: ManagedConversationEntry[] = [];
  const messageIndexes = new Map<string, number>();
  const thinkingIndexes = new Map<string, number>();
  const toolIndexes = new Map<string, number>();
  let turnThinking:
    | { index: number; streamedText: string; canonicalText: string }
    | undefined;

  const ensureTurnThinking = (key: string, thinkingId?: string) => {
    let thinking = thinkingId ? thinkingIndexes.get(thinkingId) : undefined;
    if (thinking === undefined) thinking = turnThinking?.index;
    if (thinking === undefined) {
      thinking = entries.length;
      entries.push({ key, kind: "thinking", text: "" });
      turnThinking = { index: thinking, streamedText: "", canonicalText: "" };
    } else if (turnThinking?.index !== thinking) {
      turnThinking = { index: thinking, streamedText: "", canonicalText: "" };
    }
    if (thinkingId) thinkingIndexes.set(thinkingId, thinking);
    return turnThinking;
  };

  const updateThinkingText = () => {
    if (!turnThinking) return;
    const entry = entries[turnThinking.index];
    if (entry?.kind !== "thinking") return;
    entry.text =
      turnThinking.canonicalText.length >= turnThinking.streamedText.length
        ? turnThinking.canonicalText
        : turnThinking.streamedText;
  };

  for (const event of events) {
    const key = eventKey(event);
    if (event.type === "user.message") {
      turnThinking = undefined;
      const text = textContent(event.content);
      if (text) entries.push({ key, kind: "message", role: "user", text });
      continue;
    }
    if (event.type === "agent.message_stream_start" && event.message_id) {
      messageIndexes.set(event.message_id, entries.length);
      entries.push({ key, kind: "message", role: "assistant", text: "", streaming: true });
      continue;
    }
    if (event.type === "agent.message_chunk" && event.message_id) {
      let index = messageIndexes.get(event.message_id);
      if (index === undefined) {
        index = entries.length;
        messageIndexes.set(event.message_id, index);
        entries.push({ key, kind: "message", role: "assistant", text: "", streaming: true });
      }
      const entry = entries[index];
      if (entry?.kind === "message") entry.text += event.delta ?? "";
      continue;
    }
    if (event.type === "agent.message_stream_end" && event.message_id) {
      const index = messageIndexes.get(event.message_id);
      const entry = index === undefined ? undefined : entries[index];
      if (entry?.kind === "message") entry.streaming = false;
      continue;
    }
    if (event.type === "agent.message") {
      const text = textContent(event.content);
      const index = event.message_id ? messageIndexes.get(event.message_id) : undefined;
      const entry = { key, kind: "message", role: "assistant", text } as const;
      if (index === undefined) entries.push(entry);
      else entries[index] = entry;
      continue;
    }
    if (event.type === "agent.thinking_stream_start" && event.thinking_id) {
      const thinking = ensureTurnThinking(key, event.thinking_id);
      const entry = entries[thinking.index];
      if (entry?.kind === "thinking") entry.streaming = true;
      continue;
    }
    if (event.type === "agent.thinking_chunk" && event.thinking_id) {
      const thinking = ensureTurnThinking(key, event.thinking_id);
      thinking.streamedText += event.delta ?? "";
      const entry = entries[thinking.index];
      if (entry?.kind === "thinking") entry.streaming = true;
      updateThinkingText();
      continue;
    }
    if (event.type === "agent.thinking_stream_end" && event.thinking_id) {
      const index = thinkingIndexes.get(event.thinking_id);
      const entry = index === undefined ? undefined : entries[index];
      if (entry?.kind === "thinking") entry.streaming = false;
      continue;
    }
    if (event.type === "agent.thinking") {
      const text = event.text ?? textContent(event.content);
      if (!text) continue;
      const thinking = ensureTurnThinking(key, event.thinking_id);
      thinking.canonicalText += text;
      const entry = entries[thinking.index];
      if (entry?.kind === "thinking") entry.streaming = false;
      updateThinkingText();
      continue;
    }
    if (event.type === "agent.tool_use") {
      const toolKey = event.id ?? key;
      toolIndexes.set(toolKey, entries.length);
      entries.push({ key, kind: "tool", name: event.name || "工具", state: "running" });
      continue;
    }
    if (event.type === "agent.tool_result" && event.tool_use_id) {
      const index = toolIndexes.get(event.tool_use_id);
      const entry = index === undefined ? undefined : entries[index];
      if (entry?.kind === "tool") entry.state = "completed";
      continue;
    }
    if (event.type === "session.error") {
      entries.push({ key, kind: "error", text: safeEventError(event) });
    }
  }
  return entries;
}
