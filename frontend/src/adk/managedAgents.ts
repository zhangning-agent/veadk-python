import { parseSSE } from "./sse";
import { DEFAULT_REQUEST_TIMEOUT_MS, requestSignal } from "./timeout";

export interface ManagedAgentModel {
  id: string;
  effort?: string | null;
  inference_geo?: string | null;
  speed?: string | null;
}

export interface ManagedAgent {
  id: string;
  type: "agent";
  name: string;
  description?: string | null;
  model: ManagedAgentModel;
  system?: string | null;
  tools?: unknown[];
  version: number;
  created_at?: string;
  updated_at?: string;
}

export interface ManagedEnvironment {
  id: string;
  type: "environment";
  name: string;
  description?: string | null;
  status: string;
  config: {
    type: "selfhostsandbox";
    provider: "docker";
  };
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export type ManagedSessionStatus =
  | "pending"
  | "running"
  | "idle"
  | "rescheduling"
  | "terminated";

export interface ManagedSession {
  id: string;
  type: "session";
  status: ManagedSessionStatus;
  agent_id?: string;
  agent?: ManagedAgent | string;
  environment_id?: string;
  title?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ManagedTextContent {
  type: "text";
  text: string;
}

export interface ManagedOpaqueContent {
  type: string;
  [key: string]: unknown;
}

export type ManagedContentBlock = ManagedTextContent | ManagedOpaqueContent;

export interface ManagedAgentEvent {
  type: string;
  id?: string;
  seq?: number;
  processed_at?: string;
  parent_event_id?: string;
  content?: ManagedContentBlock[] | string;
  message_id?: string;
  thinking_id?: string;
  tool_use_id?: string;
  name?: string;
  text?: string;
  delta?: string;
  status?: string;
  stop_reason?: {
    type?: string;
    event_ids?: string[];
    action_type?: string;
  };
  error?: string | {
    type?: string;
    message?: string;
    retry_status?: string;
  };
  [key: string]: unknown;
}

export interface ManagedPage<T> {
  data: T[];
  has_more?: boolean;
  next_page?: string | null;
  next_cursor?: string | null;
}

export interface CreateManagedAgentInput {
  name: string;
  model: { id: string };
  system?: string;
  tools: unknown[];
}

export interface CreateManagedSessionInput {
  agent: string;
  environment_id: string;
  title?: string;
}

export interface CreateManagedEnvironmentInput {
  name: string;
  description?: string;
  provider?: "docker";
}

export interface ManagedAgentsClientOptions {
  controlBasePath?: string;
  taskBasePath?: string;
  fetch?: typeof globalThis.fetch;
  requestTimeoutMs?: number;
}

export interface ManagedListOptions {
  limit?: number;
  page?: string;
  signal?: AbortSignal;
}

export interface ManagedEventListOptions extends ManagedListOptions {
  order?: "asc" | "desc";
  afterSeq?: number;
}

export interface ManagedEventStreamOptions {
  signal?: AbortSignal;
  lastEventId?: string;
  replay?: boolean;
  onOpen?: () => void;
}

const MAX_ERROR_BODY_LENGTH = 500;

function normalizeBasePath(value: string): string {
  const path = value.trim();
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
    throw new Error("Managed Agents API 路径必须是同源绝对路径。");
  }
  return path.replace(/\/+$/, "") || "/v1";
}

function encodeSegment(value: string): string {
  return encodeURIComponent(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function redactErrorText(value: string): string {
  return value
    .replace(/Bearer\s+[A-Za-z0-9._~+/\\-]+/gi, "Bearer <redacted>")
    .replace(
      /(["']?(?:api[_-]?key|token|secret|password|authorization)["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi,
      "$1<redacted>",
    )
    .slice(0, MAX_ERROR_BODY_LENGTH);
}

function errorDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (!isRecord(value)) return "";
  if (typeof value.message === "string") return value.message;
  if (typeof value.detail === "string") return value.detail;
  return "";
}

async function responseError(response: Response, action: string): Promise<Error> {
  const contentType = response.headers.get("content-type") || "unknown";
  const text = await response.text().catch(() => "");
  let detail = "";
  if (contentType.toLowerCase().includes("json") && text) {
    try {
      const payload = JSON.parse(text) as Record<string, unknown>;
      detail = errorDetail(payload.detail) || errorDetail(payload.error) || errorDetail(payload);
    } catch {
      detail = "响应声明为 JSON，但正文无法解析";
    }
  } else if (text) {
    detail = "服务端返回非 JSON 响应（Content-Type: " + contentType + "）";
  }
  const suffix = detail ? "：" + redactErrorText(detail) : "";
  return new Error(action + "失败（HTTP " + response.status + "）" + suffix);
}

async function jsonResponse<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw await responseError(response, action);
  const contentType = response.headers.get("content-type") || "unknown";
  if (!contentType.toLowerCase().includes("json")) {
    throw new Error(
      action + "失败（HTTP " + response.status + "）：服务端返回非 JSON 响应（Content-Type: " + contentType + "）",
    );
  }
  try {
    return await response.json() as T;
  } catch {
    throw new Error(action + "失败（HTTP " + response.status + "）：JSON 响应无法解析");
  }
}

function requireId<T extends { id?: unknown }>(value: T, action: string): T & { id: string } {
  if (typeof value.id !== "string" || !value.id) {
    throw new Error(action + "失败：响应中缺少有效 ID");
  }
  return value as T & { id: string };
}

function normalizePage<T>(value: unknown, action: string): ManagedPage<T> {
  if (!isRecord(value) || !Array.isArray(value.data)) {
    throw new Error(action + "失败：响应格式无效");
  }
  return value as unknown as ManagedPage<T>;
}

export class ManagedAgentsClient {
  private readonly controlBasePath: string;
  private readonly taskBasePath: string;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly requestTimeoutMs: number;

  constructor(options: ManagedAgentsClientOptions = {}) {
    this.controlBasePath = normalizeBasePath(options.controlBasePath ?? "/api");
    this.taskBasePath = normalizeBasePath(options.taskBasePath ?? "/v1");
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  }

  private request(
    basePath: string,
    path: string,
    init: RequestInit = {},
    stream = false,
  ): Promise<Response> {
    return this.fetcher(basePath + path, {
      ...init,
      cache: "no-store",
      signal: requestSignal(init.signal, stream ? 0 : this.requestTimeoutMs),
    });
  }

  async listAgents(options: ManagedListOptions = {}): Promise<ManagedPage<ManagedAgent>> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100) });
    if (options.page) query.set("page", options.page);
    const response = await this.request(this.controlBasePath, "/agents?" + query, {
      signal: options.signal,
    });
    return normalizePage<ManagedAgent>(await jsonResponse(response, "加载 Agent"), "加载 Agent");
  }

  async createAgent(
    input: CreateManagedAgentInput,
    signal?: AbortSignal,
  ): Promise<ManagedAgent> {
    const response = await this.request(this.controlBasePath, "/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
    return requireId(await jsonResponse<ManagedAgent>(response, "创建 Agent"), "创建 Agent");
  }

  async listEnvironments(
    options: ManagedListOptions = {},
  ): Promise<ManagedPage<ManagedEnvironment>> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100) });
    const response = await this.request(this.controlBasePath, "/environments?" + query, {
      signal: options.signal,
    });
    return normalizePage<ManagedEnvironment>(
      await jsonResponse(response, "加载 Environment"),
      "加载 Environment",
    );
  }

  async createEnvironment(
    input: CreateManagedEnvironmentInput,
    signal?: AbortSignal,
  ): Promise<ManagedEnvironment> {
    const response = await this.request(this.controlBasePath, "/environments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
    return requireId(
      await jsonResponse<ManagedEnvironment>(response, "创建 Environment"),
      "创建 Environment",
    );
  }

  async listSessions(options: ManagedListOptions = {}): Promise<ManagedPage<ManagedSession>> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100) });
    if (options.page) query.set("page", options.page);
    const response = await this.request(this.controlBasePath, "/sessions?" + query, {
      signal: options.signal,
    });
    return normalizePage<ManagedSession>(
      await jsonResponse(response, "加载 Session"),
      "加载 Session",
    );
  }

  async createSession(
    input: CreateManagedSessionInput,
    signal?: AbortSignal,
  ): Promise<ManagedSession> {
    const response = await this.request(this.controlBasePath, "/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
    return requireId(
      await jsonResponse<ManagedSession>(response, "创建 Session"),
      "创建 Session",
    );
  }

  async sendUserMessage(
    sessionId: string,
    text: string,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await this.request(this.controlBasePath, "/sessions/" + encodeSegment(sessionId) + "/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        events: [{ type: "user.message", content: [{ type: "text", text }] }],
      }),
      signal,
    });
    if (!response.ok) throw await responseError(response, "发送消息");
  }

  async listEvents(
    sessionId: string,
    options: ManagedEventListOptions = {},
  ): Promise<ManagedPage<ManagedAgentEvent>> {
    const query = new URLSearchParams({
      limit: String(options.limit ?? 100),
      order: options.order ?? "asc",
    });
    if (options.page) query.set("page", options.page);
    if (options.afterSeq !== undefined) query.set("after_seq", String(options.afterSeq));
    const response = await this.request(
      this.taskBasePath,
      "/sessions/" + encodeSegment(sessionId) + "/events?" + query,
      { signal: options.signal },
    );
    return normalizePage<ManagedAgentEvent>(
      await jsonResponse(response, "加载会话事件"),
      "加载会话事件",
    );
  }

  async *streamEvents(
    sessionId: string,
    options: ManagedEventStreamOptions = {},
  ): AsyncGenerator<ManagedAgentEvent, void, unknown> {
    const query = new URLSearchParams();
    if (options.replay !== false) query.set("replay", "1");
    const suffix = query.size ? "?" + query : "";
    const response = await this.request(
      this.taskBasePath,
      "/sessions/" + encodeSegment(sessionId) + "/events/stream" + suffix,
      {
        headers: {
          Accept: "text/event-stream",
          ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
        },
        signal: options.signal,
      },
      true,
    );
    if (!response.ok) throw await responseError(response, "连接会话事件流");
    const contentType = response.headers.get("content-type") || "unknown";
    if (!contentType.toLowerCase().includes("text/event-stream")) {
      throw new Error(
        "连接会话事件流失败（HTTP " + response.status + "）：服务端返回了 " + contentType,
      );
    }
    options.onOpen?.();
    for await (const value of parseSSE(response)) {
      if (!isRecord(value) || typeof value.type !== "string") {
        throw new Error("会话事件流返回了无效事件");
      }
      yield value as ManagedAgentEvent;
    }
  }
}
