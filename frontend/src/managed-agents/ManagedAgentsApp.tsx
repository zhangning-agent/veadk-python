import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  ManagedAgentsClient,
  type ManagedAgent,
  type ManagedAgentEvent,
  type ManagedEnvironment,
  type ManagedSession,
} from "../adk/managedAgents";
import { CompactComposer } from "../ui/CompactComposer";
import { Markdown } from "../ui/Markdown";
import { TextShimmer } from "../ui/text-shimmer/TextShimmer";
import type { ManagedAgentsRuntimeConfig } from "./config";
import {
  emptyManagedEventState,
  managedConversationEntries,
  mergeManagedEvents,
  type ManagedEventState,
} from "./eventReducer";

type ResourceKind = "agents" | "environments" | "sessions";
type TransportState = "connecting" | "live" | "polling" | "failed" | "cancelled";
type ModalKind = ResourceKind | null;

const SSE_RETRY_DELAYS_MS = [500, 1_000, 2_000] as const;

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = () => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    };
    const timer = window.setTimeout(finish, milliseconds);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  });
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function statusLabel(status: ManagedEventState["status"]): string {
  if (status === "pending") return "等待处理";
  if (status === "running") return "正在运行";
  if (status === "rescheduling") return "正在重新调度";
  if (status === "terminated") return "已结束";
  if (status === "failed") return "运行失败";
  return "可以发送";
}

function transportLabel(transport: TransportState): string {
  if (transport === "live") return "实时连接";
  if (transport === "polling") return "轮询同步";
  if (transport === "failed") return "连接异常";
  if (transport === "cancelled") return "已停止接收";
  return "正在连接";
}

function displayDate(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function listAllEvents(
  client: ManagedAgentsClient,
  sessionId: string,
  signal: AbortSignal,
  afterSeq?: number,
): Promise<ManagedAgentEvent[]> {
  const events: ManagedAgentEvent[] = [];
  let page: string | undefined;
  do {
    const result = await client.listEvents(sessionId, {
      limit: 100,
      order: "asc",
      page,
      afterSeq,
      signal,
    });
    events.push(...result.data);
    page = result.next_page ?? result.next_cursor ?? undefined;
  } while (page && !signal.aborted);
  return events;
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="managed-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="managed-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="managed-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2 id="managed-modal-title">{title}</h2>
          <button type="button" onClick={onClose} aria-label="关闭">关闭</button>
        </header>
        {children}
      </section>
    </div>
  );
}

export interface ManagedAgentsAppProps {
  config: ManagedAgentsRuntimeConfig;
}

export function ManagedAgentsApp({ config }: ManagedAgentsAppProps) {
  const client = useMemo(
    () => new ManagedAgentsClient({
      controlBasePath: config.controlBasePath,
      taskBasePath: config.taskBasePath,
    }),
    [config.controlBasePath, config.taskBasePath],
  );
  const [resource, setResource] = useState<ResourceKind>("sessions");
  const [modal, setModal] = useState<ModalKind>(null);
  const [agents, setAgents] = useState<ManagedAgent[]>([]);
  const [environments, setEnvironments] = useState<ManagedEnvironment[]>([]);
  const [sessions, setSessions] = useState<ManagedSession[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [loadingDirectory, setLoadingDirectory] = useState(true);
  const [directoryError, setDirectoryError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);

  const [agentName, setAgentName] = useState("");
  const [modelId, setModelId] = useState("doubao-seed-evolving");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [bashEnabled, setBashEnabled] = useState(true);
  const [environmentName, setEnvironmentName] = useState("");
  const [environmentDescription, setEnvironmentDescription] = useState("");
  const [sessionAgentId, setSessionAgentId] = useState("");
  const [sessionEnvironmentId, setSessionEnvironmentId] = useState("");
  const [sessionTitle, setSessionTitle] = useState("");

  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [turnPending, setTurnPending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [eventState, setEventState] = useState<ManagedEventState>(emptyManagedEventState());
  const eventStateRef = useRef(eventState);
  const [transport, setTransport] = useState<TransportState>("cancelled");
  const [transportError, setTransportError] = useState("");
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const operationControllers = useRef(new Set<AbortController>());
  const sendAbortRef = useRef<AbortController | null>(null);
  const pendingAfterSequenceRef = useRef<number | undefined | null>(null);
  const connectionGenerationRef = useRef(0);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const selectedSessionIdRef = useRef(selectedSessionId);
  selectedSessionIdRef.current = selectedSessionId;

  const selectedAgent = agents.find((item) => item.id === selectedAgentId);
  const selectedEnvironment = environments.find((item) => item.id === selectedEnvironmentId);
  const selectedSession = sessions.find((item) => item.id === selectedSessionId);
  const selectedSessionAgent = agents.find((item) => item.id === selectedSession?.agent_id);
  const selectedSessionStatus = selectedSession?.status ?? "pending";

  const runOperation = useCallback(async <T,>(operation: (signal: AbortSignal) => Promise<T>) => {
    const controller = new AbortController();
    operationControllers.current.add(controller);
    try {
      return await operation(controller.signal);
    } finally {
      operationControllers.current.delete(controller);
    }
  }, []);

  const loadDirectory = useCallback(async (signal?: AbortSignal) => {
    setLoadingDirectory(true);
    setDirectoryError("");
    try {
      const [agentPage, environmentPage, sessionPage] = await Promise.all([
        client.listAgents({ signal }),
        client.listEnvironments({ signal }),
        client.listSessions({ signal }),
      ]);
      if (signal?.aborted) return;
      setAgents(agentPage.data);
      setEnvironments(environmentPage.data);
      setSessions(sessionPage.data);
      setSelectedAgentId((value) => value || agentPage.data[0]?.id || "");
      setSelectedEnvironmentId((value) => value || environmentPage.data[0]?.id || "");
      setSessionAgentId((value) => value || agentPage.data[0]?.id || "");
      setSessionEnvironmentId((value) => value || environmentPage.data[0]?.id || "");
    } catch (error) {
      if (!signal?.aborted) setDirectoryError(messageFor(error, "加载资源失败"));
    } finally {
      if (!signal?.aborted) setLoadingDirectory(false);
    }
  }, [client]);

  useEffect(() => {
    const controller = new AbortController();
    void loadDirectory(controller.signal);
    return () => controller.abort();
  }, [loadDirectory]);

  useEffect(() => () => {
    sendAbortRef.current?.abort();
    for (const controller of operationControllers.current) controller.abort();
    operationControllers.current.clear();
  }, []);

  const updateEventState = useCallback((events: readonly ManagedAgentEvent[]) => {
    const previous = eventStateRef.current;
    const knownIds = new Set(previous.events.map((event) => event.id).filter(Boolean));
    const after = pendingAfterSequenceRef.current;
    const terminal = after !== null && events.some((event) => {
      if (!["session.status_idle", "session.status_terminated", "session.error"].includes(event.type)) return false;
      if (typeof event.seq === "number") return after === undefined || event.seq > after;
      return Boolean(event.id && !knownIds.has(event.id));
    });
    if (terminal) {
      pendingAfterSequenceRef.current = null;
      setTurnPending(false);
    }
    setEventState((current) => {
      const next = mergeManagedEvents(current, events);
      eventStateRef.current = next;
      return next;
    });
  }, []);

  useEffect(() => {
    sendAbortRef.current?.abort(new DOMException("Session changed", "AbortError"));
    sendAbortRef.current = null;
    pendingAfterSequenceRef.current = null;
    setSending(false);
    setTurnPending(false);
    const initial = emptyManagedEventState(selectedSessionStatus);
    eventStateRef.current = initial;
    setEventState(initial);
    setTransportError("");
    setSendError("");
    if (!selectedSessionId) {
      setTransport("cancelled");
      return;
    }

    const controller = new AbortController();
    const generation = ++connectionGenerationRef.current;
    const active = () => !controller.signal.aborted && generation === connectionGenerationRef.current;
    const poll = async () => {
      setTransport("polling");
      while (!controller.signal.aborted) {
        try {
          const events = await listAllEvents(client, selectedSessionId, controller.signal, eventStateRef.current.lastSequence);
          if (!active()) return;
          updateEventState(events);
          setTransportError("");
          await abortableDelay(config.pollingIntervalMs, controller.signal);
        } catch (error) {
          if (controller.signal.aborted || isAbort(error)) return;
          setTransport("failed");
          setTransportError(messageFor(error, "轮询会话事件失败"));
          await abortableDelay(Math.max(config.pollingIntervalMs, 2_000), controller.signal).catch(() => undefined);
        }
      }
    };
    const connect = async () => {
      if (!config.sseEnabled) return poll();
      setTransport("connecting");
      for (let attempt = 0; attempt <= SSE_RETRY_DELAYS_MS.length; attempt += 1) {
        try {
          const lastEventId = eventStateRef.current.lastEventId;
          for await (const event of client.streamEvents(selectedSessionId, {
            signal: controller.signal,
            lastEventId,
            replay: lastEventId === undefined,
            onOpen: () => { if (active()) { setTransport("live"); setTransportError(""); } },
          })) {
            if (!active()) return;
            updateEventState([event]);
          }
          if (controller.signal.aborted) return;
          throw new Error("实时连接已提前关闭");
        } catch (error) {
          if (controller.signal.aborted || isAbort(error)) return;
          if (attempt === SSE_RETRY_DELAYS_MS.length) {
            setTransportError(messageFor(error, "实时连接不可用，已切换为轮询同步"));
            return poll();
          }
          setTransport("connecting");
          setTransportError("实时连接中断，正在重新连接…");
          await abortableDelay(SSE_RETRY_DELAYS_MS[attempt], controller.signal).catch(() => undefined);
        }
      }
    };
    void connect();
    return () => {
      controller.abort(new DOMException("Session changed", "AbortError"));
      setTransport("cancelled");
    };
  }, [client, config.pollingIntervalMs, config.sseEnabled, connectionAttempt, selectedSessionId, selectedSessionStatus, updateEventState]);

  const entries = useMemo(() => managedConversationEntries(eventState.events), [eventState.events]);
  const turnBusy = sending || turnPending || eventState.status === "running" || eventState.status === "rescheduling";
  const composerDisabled = !selectedSessionId || transport === "cancelled" || eventState.status === "terminated";

  useEffect(() => {
    const element = transcriptRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [entries]);

  async function submitAgent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!agentName.trim() || !modelId.trim() || busy) return;
    setBusy(true); setActionError("");
    try {
      const agent = await runOperation((signal) => client.createAgent({
        name: agentName.trim(),
        model: { id: modelId.trim() },
        system: systemPrompt.trim() || undefined,
        tools: bashEnabled ? [{
          type: "agent_toolset_20260401",
          default_config: { enabled: false, permission_policy: { type: "always_allow" } },
          configs: [{ type: "bash", name: "bash", enabled: true, permission_policy: { type: "always_allow" } }],
        }] : [],
      }, signal));
      setAgents((items) => [agent, ...items.filter((item) => item.id !== agent.id)]);
      setSelectedAgentId(agent.id);
      setSessionAgentId(agent.id);
      setAgentName(""); setSystemPrompt(""); setModal(null);
    } catch (error) { if (!isAbort(error)) setActionError(messageFor(error, "创建 Agent 失败")); }
    finally { setBusy(false); }
  }

  async function submitEnvironment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!environmentName.trim() || busy) return;
    setBusy(true); setActionError("");
    try {
      const environment = await runOperation((signal) => client.createEnvironment({
        name: environmentName.trim(),
        description: environmentDescription.trim() || undefined,
        provider: "docker",
      }, signal));
      setEnvironments((items) => [environment, ...items.filter((item) => item.id !== environment.id)]);
      setSelectedEnvironmentId(environment.id);
      setSessionEnvironmentId(environment.id);
      setEnvironmentName(""); setEnvironmentDescription(""); setModal(null);
    } catch (error) { if (!isAbort(error)) setActionError(messageFor(error, "创建 Environment 失败")); }
    finally { setBusy(false); }
  }

  async function submitSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionAgentId || !sessionEnvironmentId || busy) return;
    setBusy(true); setActionError("");
    try {
      const session = await runOperation((signal) => client.createSession({
        agent: sessionAgentId,
        environment_id: sessionEnvironmentId,
        title: sessionTitle.trim() || undefined,
      }, signal));
      setSessions((items) => [session, ...items.filter((item) => item.id !== session.id)]);
      setSelectedSessionId(session.id);
      setResource("sessions");
      setSessionTitle(""); setModal(null);
    } catch (error) { if (!isAbort(error)) setActionError(messageFor(error, "创建 Session 失败")); }
    finally { setBusy(false); }
  }

  async function submitMessage() {
    const message = input.trim();
    if (!message || !selectedSessionId || turnBusy || composerDisabled) return;
    const sessionId = selectedSessionId;
    const controller = new AbortController();
    sendAbortRef.current?.abort();
    sendAbortRef.current = controller;
    pendingAfterSequenceRef.current = eventStateRef.current.lastSequence;
    setSending(true); setTurnPending(true); setSendError("");
    setEventState((current) => {
      const next = { ...current, status: "running" as const };
      eventStateRef.current = next;
      return next;
    });
    try {
      await client.sendUserMessage(sessionId, message, controller.signal);
      if (!controller.signal.aborted && selectedSessionIdRef.current === sessionId) {
        setInput((value) => value.trim() === message ? "" : value);
      }
    } catch (error) {
      if (!isAbort(error) && selectedSessionIdRef.current === sessionId) {
        pendingAfterSequenceRef.current = null; setTurnPending(false);
        setSendError(messageFor(error, "消息发送结果未知。请刷新事件后确认是否已提交，不要直接重复发送。"));
      }
    } finally {
      if (sendAbortRef.current === controller) { sendAbortRef.current = null; setSending(false); }
    }
  }

  const items = resource === "agents" ? agents : resource === "environments" ? environments : sessions;
  const resourceTitle = resource === "agents" ? "Agents" : resource === "environments" ? "Environments" : "Sessions";
  const createLabel = resource === "agents" ? "新建 Agent" : resource === "environments" ? "新建 Environment" : "新建 Session";
  const selectedId = resource === "agents" ? selectedAgentId : resource === "environments" ? selectedEnvironmentId : selectedSessionId;
  const selectItem = (id: string) => {
    if (resource === "agents") setSelectedAgentId(id);
    else if (resource === "environments") setSelectedEnvironmentId(id);
    else setSelectedSessionId(id);
  };
  const dispatcherCommand = selectedEnvironment
    ? `cd /home/mofanke/gitcode/actb-mono/sandboxes/self-host-sandbox\nexport MANAGED_AGENT_API_MODE=oma\nexport MANAGED_AGENT_AUTH_HEADER=authorization\nSANDBOX_PROVIDER=docker ENV_FILE=/home/mofanke/github/veadk-python-zhangning/examples/16_self_host_sandbox/.env ENVIRONMENT_ID_OVERRIDE=${selectedEnvironment.id} IMAGE_VERSION=0.0.7 ./run-local.sh`
    : "";

  return (
    <div className="managed-workbench">
      <aside className="managed-nav">
        <header><h1>{config.title}</h1><p>本地资源 · 云端会话</p></header>
        <nav aria-label="资源导航">
          {(["agents", "environments", "sessions"] as const).map((kind) => (
            <button key={kind} type="button" className={resource === kind ? "is-active" : ""} onClick={() => setResource(kind)}>
              <span>{kind === "agents" ? "Agents" : kind === "environments" ? "Environments" : "Sessions"}</span>
              <small>{kind === "agents" ? agents.length : kind === "environments" ? environments.length : sessions.length}</small>
            </button>
          ))}
        </nav>
        <footer><span className="managed-cloud-dot" />Task Server 云端连接</footer>
      </aside>

      <section className="managed-directory" aria-label={resourceTitle + " 列表"}>
        <header className="managed-directory-header">
          <div><h2>{resourceTitle}</h2><p>{resource === "sessions" ? "云端会话索引" : "本地持久化资源"}</p></div>
          <button type="button" className="managed-primary" onClick={() => { setActionError(""); setModal(resource); }}>{createLabel}</button>
        </header>
        <div className="managed-directory-toolbar">
          <button type="button" onClick={() => void loadDirectory()} disabled={loadingDirectory}>刷新</button>
          <span>{items.length} 项</span>
        </div>
        {directoryError ? <p className="managed-error" role="alert">{directoryError}</p> : null}
        <div className="managed-resource-list" role="listbox" aria-label={resourceTitle}>
          {loadingDirectory ? <TextShimmer as="p">正在加载资源…</TextShimmer> : null}
          {!loadingDirectory && items.length === 0 ? <div className="managed-empty"><h3>暂无 {resourceTitle}</h3><p>使用右上角按钮创建第一项。</p></div> : null}
          {items.map((item) => {
            const name = "name" in item ? item.name : item.title || "未命名 Session";
            const subtitle = resource === "agents"
              ? (item as ManagedAgent).model.id
              : resource === "environments"
                ? (item as ManagedEnvironment).id
                : `${(item as ManagedSession).environment_id || "—"} · ${(item as ManagedSession).status}`;
            return <button key={item.id} type="button" role="option" aria-selected={selectedId === item.id} className={selectedId === item.id ? "is-selected" : ""} onClick={() => selectItem(item.id)}>
              <strong>{name}</strong><span>{subtitle}</span><small>{displayDate(item.created_at)}</small>
            </button>;
          })}
        </div>
      </section>

      <main className="managed-detail">
        {resource === "agents" ? (
          selectedAgent ? <section className="managed-resource-detail"><header><div><p>Agent</p><h2>{selectedAgent.name}</h2></div><span className="managed-status">本地</span></header><dl><div><dt>ID</dt><dd>{selectedAgent.id}</dd></div><div><dt>模型</dt><dd>{selectedAgent.model.id}</dd></div><div><dt>版本</dt><dd>{selectedAgent.version}</dd></div><div><dt>系统提示词</dt><dd>{selectedAgent.system || "—"}</dd></div><div><dt>工具</dt><dd>{selectedAgent.tools?.length ? "Bash 已启用" : "未启用"}</dd></div></dl></section> : <div className="managed-detail-empty">选择一个 Agent 查看详情</div>
        ) : resource === "environments" ? (
          selectedEnvironment ? <section className="managed-resource-detail"><header><div><p>Environment</p><h2>{selectedEnvironment.name}</h2></div><span className="managed-status">本地 Docker</span></header><dl><div><dt>ID</dt><dd>{selectedEnvironment.id}</dd></div><div><dt>状态</dt><dd>{selectedEnvironment.status}</dd></div><div><dt>说明</dt><dd>{selectedEnvironment.description || "—"}</dd></div></dl><div className="managed-command"><div><h3>启动 Dispatcher</h3><p>在运行 Docker 的开发机上执行。凭据从指定 .env 读取，不显示在页面中。</p></div><pre>{dispatcherCommand}</pre><button type="button" onClick={() => void navigator.clipboard.writeText(dispatcherCommand)}>复制命令</button></div></section> : <div className="managed-detail-empty">选择一个 Environment 查看启动方式</div>
        ) : selectedSession ? (
          <section className="managed-conversation" aria-label="Managed Agents 对话">
            <header className="managed-conversation-header"><div><p>Session</p><h2>{selectedSession.title || "未命名 Session"}</h2><span>{selectedSession.id} · {selectedSession.environment_id} · {statusLabel(eventState.status)}</span></div><div className="managed-transport" aria-live="polite"><i className={"is-" + transport} />{transportLabel(transport)}{transport === "failed" || transport === "cancelled" ? <button type="button" onClick={() => setConnectionAttempt((value) => value + 1)}>重连</button> : null}</div></header>
            {transportError ? <p className="managed-transport-error" role="alert">{transportError}</p> : null}
            <div className="managed-transcript" ref={transcriptRef} aria-live="polite">
              {entries.length === 0 ? <div className="managed-detail-empty">{transport === "connecting" ? <TextShimmer as="span">正在读取云端事件…</TextShimmer> : "发送第一条消息开始对话。"}</div> : entries.map((entry) => {
                if (entry.kind === "message") return <article className={"managed-message is-" + entry.role} key={entry.key}><span>{entry.role === "user" ? "你" : selectedSessionAgent?.name || "Agent"}</span>{entry.text ? <Markdown text={entry.text} allowRawHtml={false} streaming={entry.streaming} /> : <TextShimmer as="span">正在生成…</TextShimmer>}</article>;
                if (entry.kind === "thinking") return <details className="managed-thinking" key={entry.key}><summary>思考过程</summary><p>{entry.text}</p></details>;
                if (entry.kind === "tool") return <div className="managed-tool" key={entry.key}><strong>{entry.name}</strong><span>{entry.state === "completed" ? "已完成" : "执行中"}</span></div>;
                return <p className="managed-error" role="alert" key={entry.key}>{entry.text}</p>;
              })}
            </div>
            <div className="managed-composer">{sendError ? <p className="managed-error" role="alert">{sendError}</p> : null}<CompactComposer value={input} onChange={setInput} onSubmit={submitMessage} busy={turnBusy} disabled={composerDisabled} placeholder="向 Agent 发送消息…" /></div>
          </section>
        ) : <div className="managed-detail-empty">选择一个 Session 开始对话</div>}
      </main>

      {modal === "agents" ? <Modal title="新建 Agent" onClose={() => !busy && setModal(null)}><form className="managed-form" onSubmit={submitAgent}><label><span>名称</span><input value={agentName} onChange={(event) => setAgentName(event.target.value)} required autoFocus /></label><label><span>模型 ID</span><input value={modelId} onChange={(event) => setModelId(event.target.value)} required /></label><label><span>系统提示词</span><textarea rows={4} value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} /></label><label className="managed-check"><input type="checkbox" checked={bashEnabled} onChange={(event) => setBashEnabled(event.target.checked)} /><span>启用 Bash 工具</span></label>{actionError ? <p className="managed-error" role="alert">{actionError}</p> : null}<footer><button type="button" onClick={() => setModal(null)}>取消</button><button className="managed-primary" disabled={busy || !agentName.trim() || !modelId.trim()}>{busy ? "正在创建…" : "创建 Agent"}</button></footer></form></Modal> : null}
      {modal === "environments" ? <Modal title="新建 Environment" onClose={() => !busy && setModal(null)}><form className="managed-form" onSubmit={submitEnvironment}><label><span>名称</span><input value={environmentName} onChange={(event) => setEnvironmentName(event.target.value)} required autoFocus /></label><label><span>说明</span><textarea rows={3} value={environmentDescription} onChange={(event) => setEnvironmentDescription(event.target.value)} /></label><label><span>执行方式</span><input value="本地 Docker" disabled /></label>{actionError ? <p className="managed-error" role="alert">{actionError}</p> : null}<footer><button type="button" onClick={() => setModal(null)}>取消</button><button className="managed-primary" disabled={busy || !environmentName.trim()}>{busy ? "正在创建…" : "创建 Environment"}</button></footer></form></Modal> : null}
      {modal === "sessions" ? <Modal title="新建 Session" onClose={() => !busy && setModal(null)}><form className="managed-form" onSubmit={submitSession}><label><span>Agent</span><select value={sessionAgentId} onChange={(event) => setSessionAgentId(event.target.value)} required><option value="">请选择 Agent</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label><label><span>Environment</span><select value={sessionEnvironmentId} onChange={(event) => setSessionEnvironmentId(event.target.value)} required><option value="">请选择 Environment</option>{environments.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label><label><span>标题</span><input value={sessionTitle} onChange={(event) => setSessionTitle(event.target.value)} placeholder="可选" /></label>{agents.length === 0 || environments.length === 0 ? <p className="managed-hint">创建 Session 前需要至少一个 Agent 和一个 Environment。</p> : null}{actionError ? <p className="managed-error" role="alert">{actionError}</p> : null}<footer><button type="button" onClick={() => setModal(null)}>取消</button><button className="managed-primary" disabled={busy || !sessionAgentId || !sessionEnvironmentId}>{busy ? "正在创建…" : "创建 Session"}</button></footer></form></Modal> : null}
    </div>
  );
}
