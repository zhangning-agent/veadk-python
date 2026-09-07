export interface ManagedAgentsRuntimeConfig {
  controlBasePath: string;
  taskBasePath: string;
  pollingIntervalMs: number;
  sseEnabled: boolean;
  title: string;
}

export const DEFAULT_MANAGED_AGENTS_CONFIG: ManagedAgentsRuntimeConfig = {
  controlBasePath: "/api",
  taskBasePath: "/v1",
  pollingIntervalMs: 2_000,
  sseEnabled: true,
  title: "托管智能体",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export async function loadManagedAgentsConfig(
  signal?: AbortSignal,
): Promise<ManagedAgentsRuntimeConfig> {
  const response = await fetch("/managed-agents-config.json", {
    cache: "no-store",
    signal,
  });
  if (response.status === 404) return DEFAULT_MANAGED_AGENTS_CONFIG;
  if (!response.ok) {
    throw new Error("加载页面配置失败（HTTP " + response.status + "）");
  }
  const value = await response.json() as unknown;
  if (!isRecord(value)) throw new Error("页面配置必须是 JSON 对象");

  const path = (name: "controlBasePath" | "taskBasePath") => {
    const candidate = typeof value[name] === "string"
      ? value[name].trim()
      : DEFAULT_MANAGED_AGENTS_CONFIG[name];
    if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.includes("://")) {
      throw new Error("页面配置中的 API 路径必须是同源绝对路径");
    }
    return candidate;
  };

  const pollingIntervalMs = typeof value.pollingIntervalMs === "number"
    ? value.pollingIntervalMs
    : DEFAULT_MANAGED_AGENTS_CONFIG.pollingIntervalMs;
  if (!Number.isFinite(pollingIntervalMs) || pollingIntervalMs < 500 || pollingIntervalMs > 30_000) {
    throw new Error("页面配置中的轮询间隔必须在 500 到 30000 毫秒之间");
  }

  const title = typeof value.title === "string" && value.title.trim()
    ? value.title.trim().slice(0, 48)
    : DEFAULT_MANAGED_AGENTS_CONFIG.title;
  return {
    controlBasePath: path("controlBasePath"),
    taskBasePath: path("taskBasePath"),
    pollingIntervalMs,
    sseEnabled: value.sseEnabled !== false,
    title,
  };
}
