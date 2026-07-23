import { Copy, ExternalLink, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { OpenClawSession } from "../adk/openclaw";
import "./OpenClawWorkspace.css";

function duration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return `${hours ? `${hours}小时 ` : ""}${minutes}分 ${secs.toString().padStart(2, "0")}秒`;
}

export function OpenClawLifecycle({ session }: { session: OpenClawSession }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [copied, setCopied] = useState(false);
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
      <span className="openclaw-lifecycle__id" title={session.sandboxId}>
        ID {session.sandboxId}
      </span>
      <button
        type="button"
        title="复制沙箱 ID"
        aria-label="复制沙箱 ID"
        onClick={() => {
          void navigator.clipboard.writeText(session.sandboxId).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          });
        }}
      >
        <Copy size={13} />
        {copied ? <span className="openclaw-lifecycle__copied">已复制</span> : null}
      </button>
    </div>
  );
}

export function OpenClawWorkspace({
  session,
  onExit,
}: {
  session: OpenClawSession;
  onExit: () => void;
}) {
  const [loaded, setLoaded] = useState(false);
  const safePreviewUrl = useMemo(() => session.previewUrl, [session.previewUrl]);
  return (
    <section className="openclaw-workspace">
      <header className="openclaw-workspace__header">
        <div>
          <span className="openclaw-workspace__brand">OpenClaw</span>
          <span className="openclaw-workspace__sandbox">独立沙箱</span>
        </div>
        <div className="openclaw-workspace__actions">
          <a href={safePreviewUrl} target="_blank" rel="noreferrer" title="在新窗口打开">
            <ExternalLink size={15} />
          </a>
          <button type="button" onClick={onExit} title="关闭并销毁沙箱">
            <X size={17} />
          </button>
        </div>
      </header>
      {!loaded ? (
        <div className="openclaw-workspace__loading">
          <Loader2 className="spin" />
          正在载入 OpenClaw…
        </div>
      ) : null}
      <iframe
        title="OpenClaw"
        src={safePreviewUrl}
        allow="clipboard-read; clipboard-write"
        onLoad={() => setLoaded(true)}
      />
    </section>
  );
}
