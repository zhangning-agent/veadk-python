import "../styles.css";
import "./managed-agents.css";

import { StrictMode, useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { MotionConfig } from "motion/react";
import { PhotoProvider } from "react-photo-view";
import "react-photo-view/dist/react-photo-view.css";

import { ManagedAgentsApp } from "./ManagedAgentsApp";
import {
  loadManagedAgentsConfig,
  type ManagedAgentsRuntimeConfig,
} from "./config";

function ManagedAgentsBootstrap() {
  const [config, setConfig] = useState<ManagedAgentsRuntimeConfig | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void loadManagedAgentsConfig(controller.signal)
      .then((value) => {
        document.title = value.title;
        setConfig(value);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "加载页面配置失败");
        }
      });
    return () => controller.abort();
  }, []);

  if (error) return <main className="managed-agents-boot-error" role="alert">{error}</main>;
  if (!config) return <main className="managed-agents-boot">正在加载 Managed Agents…</main>;
  return <ManagedAgentsApp config={config} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <PhotoProvider maskOpacity={0.9}>
        <ManagedAgentsBootstrap />
      </PhotoProvider>
    </MotionConfig>
  </StrictMode>,
);
