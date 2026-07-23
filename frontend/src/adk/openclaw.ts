import { withAuth } from "./auth";
import { withLocalUser } from "./identity";
import { requestSignal } from "./timeout";

const OPENCLAW_API = "/web/openclaw/sessions";
const START_TIMEOUT_MS = 360_000;
const CLOSE_TIMEOUT_MS = 15_000;

export interface OpenClawSession {
  id: string;
  sandboxId: string;
  previewUrl: string;
  createdAt: number;
  expiresAt: number;
  ttlSeconds: number;
}

interface OpenClawSessionResponse {
  sessionId?: unknown;
  sandboxId?: unknown;
  previewUrl?: unknown;
  createdAt?: unknown;
  expiresAt?: unknown;
  ttlSeconds?: unknown;
  status?: unknown;
}

function headers(): Headers {
  const next = withLocalUser({ Accept: "application/json" });
  return next;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json() as {
      detail?: string | { message?: string };
    };
    if (typeof payload.detail === "string") return payload.detail;
    if (typeof payload.detail?.message === "string") return payload.detail.message;
  } catch {
    // Fall through to the bounded HTTP diagnostic.
  }
  return `OpenClaw 沙箱创建失败（HTTP ${response.status}）`;
}

export const openClawClient = {
  async startSession(signal?: AbortSignal): Promise<OpenClawSession> {
    const response = await fetch(withAuth(OPENCLAW_API), {
      method: "POST",
      headers: headers(),
      signal: requestSignal(signal, START_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json() as OpenClawSessionResponse;
    if (
      data.status !== "ready" ||
      typeof data.sessionId !== "string" ||
      typeof data.sandboxId !== "string" ||
      typeof data.previewUrl !== "string" ||
      typeof data.createdAt !== "number" ||
      typeof data.expiresAt !== "number" ||
      typeof data.ttlSeconds !== "number"
    ) {
      throw new Error("OpenClaw 沙箱返回了无效的会话信息");
    }
    return {
      id: data.sessionId,
      sandboxId: data.sandboxId,
      previewUrl: data.previewUrl,
      createdAt: data.createdAt,
      expiresAt: data.expiresAt,
      ttlSeconds: data.ttlSeconds,
    };
  },

  async closeSession(sessionId: string, signal?: AbortSignal): Promise<void> {
    if (!sessionId) return;
    const response = await fetch(
      withAuth(`${OPENCLAW_API}/${encodeURIComponent(sessionId)}`),
      {
        method: "DELETE",
        headers: headers(),
        signal: requestSignal(signal, CLOSE_TIMEOUT_MS),
      },
    );
    if (!response.ok && response.status !== 404) {
      throw new Error(await errorMessage(response));
    }
  },
};
