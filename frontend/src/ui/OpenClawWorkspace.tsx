import { useEffect, useState } from "react";
import type { OpenClawSession } from "../adk/openclaw";
import { SandboxBrandIcon } from "./SandboxBrandIcon";
import {
  SandboxCloseIcon,
  SandboxExternalLinkIcon,
  SandboxLoadingIcon,
  SandboxMinimizeIcon,
  SandboxOpenIcon,
  SandboxTerminalIcon,
  SandboxWebIcon,
} from "./icons/SandboxWorkspaceIcons";
import "./OpenClawWorkspace.css";

function duration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return `${hours ? `${hours}小时 ` : ""}${minutes}分 ${secs.toString().padStart(2, "0")}秒`;
}

export function OpenClawLifecycle({
  session,
  minimized,
  onOpen,
}: {
  session: OpenClawSession;
  minimized: boolean;
  onOpen: () => void;
}) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const remaining = Math.max(0, session.expiresAt - now);
  return (
    <div className="openclaw-lifecycle" aria-label="OpenClaw 沙箱生命周期">
      <span className={`openclaw-lifecycle__dot${remaining <= 0 ? " is-expired" : ""}`} />
      <span>{remaining > 0 ? `剩余 ${duration(remaining)}` : "已过期"}</span>
      <span className="openclaw-lifecycle__divider" />
      <span className="openclaw-lifecycle__brand">
        <SandboxBrandIcon
          brand="openclaw"
          className="openclaw-lifecycle__brand-icon"
        />
        <span>OpenClaw</span>
      </span>
      {minimized ? (
        <button
          type="button"
          className="openclaw-lifecycle__open"
          title="打开 OpenClaw"
          aria-label="打开 OpenClaw"
          onClick={onOpen}
        >
          <SandboxOpenIcon size={13} />
          <span>打开</span>
        </button>
      ) : null}
    </div>
  );
}

export function OpenClawWorkspace({
  session,
  onMinimize,
  onExit,
}: {
  session: OpenClawSession;
  onMinimize: () => void;
  onExit: () => void;
}) {
  const [loaded, setLoaded] = useState(false);
  const [view, setView] = useState<"webui" | "terminal">("webui");
  const activeUrl = view === "webui" ? session.webuiUrl : session.terminalUrl;
  function selectView(nextView: "webui" | "terminal") {
    if (nextView === view) return;
    setLoaded(false);
    setView(nextView);
  }
  function moveView(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const nextView = view === "webui" ? "terminal" : "webui";
    selectView(nextView);
    event.currentTarget
      .parentElement?.querySelector<HTMLButtonElement>(`[data-view="${nextView}"]`)
      ?.focus();
  }
  return (
    <section className="openclaw-workspace">
      <header className="openclaw-workspace__header">
        <div className="openclaw-workspace__identity">
          <SandboxBrandIcon brand="openclaw" />
          <span className="openclaw-workspace__brand">OpenClaw</span>
          <span className="openclaw-workspace__sandbox">
            {view === "webui" ? "WebUI" : "Terminal"}
          </span>
        </div>
        <div className="openclaw-workspace__controls">
          <div className="openclaw-workspace__views" role="tablist" aria-label="OpenClaw 视图">
            <button
              type="button"
              role="tab"
              data-view="webui"
              aria-selected={view === "webui"}
              tabIndex={view === "webui" ? 0 : -1}
              onClick={() => selectView("webui")}
              onKeyDown={moveView}
            >
              <SandboxWebIcon size={13} />
              WebUI
            </button>
            <button
              type="button"
              role="tab"
              data-view="terminal"
              aria-selected={view === "terminal"}
              tabIndex={view === "terminal" ? 0 : -1}
              onClick={() => selectView("terminal")}
              onKeyDown={moveView}
            >
              <SandboxTerminalIcon size={13} />
              Terminal
            </button>
          </div>
          <div className="openclaw-workspace__actions">
            <a href={activeUrl} target="_blank" rel="noreferrer" title="在新窗口打开">
              <SandboxExternalLinkIcon size={15} />
            </a>
            <button
              type="button"
              onClick={onMinimize}
              title="最小化 OpenClaw"
              aria-label="最小化 OpenClaw"
            >
              <SandboxMinimizeIcon size={17} />
            </button>
            <button type="button" onClick={onExit} title="关闭并销毁沙箱">
              <SandboxCloseIcon size={17} />
            </button>
          </div>
        </div>
      </header>
      {!loaded ? (
        <div className="openclaw-workspace__loading">
          <SandboxLoadingIcon className="spin" />
          正在载入 {view === "webui" ? "OpenClaw WebUI" : "Terminal"}…
        </div>
      ) : null}
      <iframe
        title={view === "webui" ? "OpenClaw WebUI" : "OpenClaw Terminal"}
        src={activeUrl}
        allow="clipboard-read; clipboard-write"
        onLoad={() => setLoaded(true)}
      />
    </section>
  );
}
