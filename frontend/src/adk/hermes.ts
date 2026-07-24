import { withAuth } from "./auth";
import { withLocalUser } from "./identity";
import { requestSignal } from "./timeout";

const HERMES_API = "/web/hermes/sessions";
const START_TIMEOUT_MS = 360_000;
const CLOSE_TIMEOUT_MS = 15_000;

export interface HermesSession {
  id: string;
  sandboxId: string;
  previewUrl: string;
  webuiUrl: string;
  terminalUrl: string;
  createdAt: number;
  expiresAt: number;
  ttlSeconds: number;
}

interface HermesSessionResponse {
  sessionId?: unknown;
  sandboxId?: unknown;
  previewUrl?: unknown;
  webuiUrl?: unknown;
  terminalUrl?: unknown;
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
  return `Hermes 沙箱创建失败（HTTP ${response.status}）`;
}

export const hermesClient = {
  async startSession(signal?: AbortSignal): Promise<HermesSession> {
    const response = await fetch(withAuth(HERMES_API), {
      method: "POST",
      headers: headers(),
      signal: requestSignal(signal, START_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json() as HermesSessionResponse;
    if (
      data.status !== "ready" ||
      typeof data.sessionId !== "string" ||
      typeof data.sandboxId !== "string" ||
      typeof data.previewUrl !== "string" ||
      typeof data.webuiUrl !== "string" ||
      typeof data.terminalUrl !== "string" ||
      typeof data.createdAt !== "number" ||
      typeof data.expiresAt !== "number" ||
      typeof data.ttlSeconds !== "number"
    ) {
      throw new Error("Hermes 沙箱返回了无效的会话信息");
    }
    return {
      id: data.sessionId,
      sandboxId: data.sandboxId,
      previewUrl: data.previewUrl,
      webuiUrl: data.webuiUrl,
      terminalUrl: data.terminalUrl,
      createdAt: data.createdAt,
      expiresAt: data.expiresAt,
      ttlSeconds: data.ttlSeconds,
    };
  },

  async closeSession(sessionId: string, signal?: AbortSignal): Promise<void> {
    if (!sessionId) return;
    const response = await fetch(
      withAuth(`${HERMES_API}/${encodeURIComponent(sessionId)}`),
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
