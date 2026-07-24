import { useEffect, useRef, useState } from "react";
import { AgentIdentityIcon } from "../AgentIdentityIcon";
import { SandboxBrandIcon } from "../SandboxBrandIcon";
import type { NewChatMode } from "./types";
import "./new-chat-modes.css";

interface ModeOption {
  value: NewChatMode;
  label: string;
  description: string;
}

const MODES: ModeOption[] = [
  {
    value: "agent",
    label: "Agent",
    description: "与当前选择的 Agent 对话",
  },
  {
    value: "temporary",
    label: "临时会话",
    description: "在 AgentKit 沙箱中执行一次性任务",
  },
  {
    value: "openclaw",
    label: "OpenClaw",
    description: "在独立沙箱中打开 OpenClaw",
  },
  {
    value: "hermes",
    label: "Hermes",
    description: "在独立沙箱中打开 Hermes",
  },
  {
    value: "skill-create",
    label: "创建 Skill",
    description: "使用两个模型生成并对比 Skill",
  },
];
const TOP_LEVEL_MODES = MODES.filter(
  (mode) => mode.value !== "openclaw" && mode.value !== "hermes",
);
const SANDBOX_MODES = MODES.filter(
  (mode) => mode.value === "openclaw" || mode.value === "hermes",
);

export interface NewChatModeSelectorProps {
  value: NewChatMode;
  agentName: string;
  onChange: (value: NewChatMode) => void;
  disabled?: boolean;
  temporaryEnabled?: boolean;
  openclawEnabled?: boolean;
  hermesEnabled?: boolean;
  skillCreateEnabled?: boolean;
}

function ModeIcon({ mode }: { mode: NewChatMode }) {
  if (mode === "skill-create") {
    return (
      <svg className="new-chat-mode__skill-icon" viewBox="0 0 20 20" aria-hidden="true">
        <path d="M10 2.2l1.35 4.1 4.15 1.35-4.15 1.35L10 13.1 8.65 9 4.5 7.65 8.65 6.3 10 2.2Z" />
        <path d="M15.6 12.2l.6 1.8 1.8.6-1.8.6-.6 1.8-.6-1.8-1.8-.6 1.8-.6.6-1.8Z" />
      </svg>
    );
  }
  if (mode === "openclaw" || mode === "hermes") {
    return <SandboxBrandIcon brand={mode} />;
  }
  if (mode === "temporary") {
    return (
      <svg className="new-chat-mode__temporary-icon" viewBox="0 0 20 20" aria-hidden="true">
        <path
          d="M4.1 4.2h11.8v8.7H9l-3.5 2.8v-2.8H4.1z"
          strokeDasharray="2.25 1.9"
        />
      </svg>
    );
  }
  return <AgentIdentityIcon className="new-chat-mode__agent-icon" />;
}

export function NewChatModeSelector({
  value,
  agentName,
  onChange,
  disabled = false,
  temporaryEnabled,
  openclawEnabled,
  hermesEnabled,
  skillCreateEnabled,
}: NewChatModeSelectorProps) {
  const [open, setOpen] = useState(false);
  const [sandboxSubmenuOpen, setSandboxSubmenuOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() =>
    Math.max(0, TOP_LEVEL_MODES.findIndex((mode) => mode.value === value)),
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const current = MODES.find((mode) => mode.value === value) ?? MODES[0];

  function modeEnabled(mode: ModeOption): boolean | undefined {
    if (mode.value === "temporary") return temporaryEnabled;
    if (mode.value === "openclaw") return openclawEnabled;
    if (mode.value === "hermes") return hermesEnabled;
    if (mode.value === "skill-create") return skillCreateEnabled;
    return true;
  }

  function modeDisabled(mode: ModeOption): boolean {
    return modeEnabled(mode) !== true;
  }

  function modeDescription(mode: ModeOption): string {
    const enabled = modeEnabled(mode);
    if (enabled === undefined) return "正在检查配置";
    if (!enabled) return "管理员未配置";
    return mode.description;
  }

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function moveActive(delta: number) {
    let next = activeIndex;
    do {
      next = (next + delta + TOP_LEVEL_MODES.length) % TOP_LEVEL_MODES.length;
    } while (modeDisabled(TOP_LEVEL_MODES[next]));
    setActiveIndex(next);
  }

  function choose(mode: ModeOption) {
    if (modeDisabled(mode)) return;
    onChange(mode.value);
    setOpen(false);
    setSandboxSubmenuOpen(false);
    triggerRef.current?.focus();
  }

  return (
    <div className="new-chat-mode" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="new-chat-mode__trigger"
        aria-label="选择新会话模式"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => {
          setActiveIndex(
            Math.max(0, TOP_LEVEL_MODES.findIndex((mode) => mode.value === value)),
          );
          setOpen((currentOpen) => !currentOpen);
          setSandboxSubmenuOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) setOpen(true);
            else moveActive(event.key === "ArrowDown" ? 1 : -1);
          } else if (open && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            choose(TOP_LEVEL_MODES[activeIndex]);
          } else if (open && event.key === "Escape") {
            event.preventDefault();
            setOpen(false);
          }
        }}
      >
        <span className="new-chat-mode__icon"><ModeIcon mode={current.value} /></span>
        <span
          className="new-chat-mode__current"
          title={current.value === "agent" ? agentName : undefined}
        >
          {current.value === "agent" ? agentName : current.label}
        </span>
        <svg className="new-chat-mode__chevron" viewBox="0 0 12 12" aria-hidden="true">
          <path d="m3 4.5 3 3 3-3" />
        </svg>
      </button>

      {open ? (
        <div
          className="new-chat-mode__menu"
          role="listbox"
          aria-label="新会话模式"
          tabIndex={-1}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              moveActive(event.key === "ArrowDown" ? 1 : -1);
            } else if (event.key === "Enter") {
              event.preventDefault();
              choose(TOP_LEVEL_MODES[activeIndex]);
            } else if (event.key === "Escape") {
              event.preventDefault();
              setOpen(false);
              triggerRef.current?.focus();
            }
          }}
        >
          {TOP_LEVEL_MODES.map((mode, index) => (
            <div key={mode.value}>
              {mode.value === "skill-create" ? (
                <div className="new-chat-mode__sandbox-menu">
                  <button
                    type="button"
                    className={`new-chat-mode__option new-chat-mode__sandbox-parent${
                      value === "openclaw" || value === "hermes" ? " is-selected" : ""
                    }`}
                    aria-haspopup="listbox"
                    aria-expanded={sandboxSubmenuOpen}
                    disabled={openclawEnabled !== true && hermesEnabled !== true}
                    onClick={() => setSandboxSubmenuOpen((currentOpen) => !currentOpen)}
                  >
                    <span className="new-chat-mode__option-icon new-chat-mode__sandbox-stack">
                      <SandboxBrandIcon brand="openclaw" />
                      <SandboxBrandIcon brand="hermes" />
                    </span>
                    <span className="new-chat-mode__copy">
                      <span className="new-chat-mode__label">Agent 沙箱</span>
                      <span>
                        {openclawEnabled === undefined || hermesEnabled === undefined
                          ? "正在检查配置"
                          : openclawEnabled || hermesEnabled
                            ? "选择 OpenClaw 或 Hermes"
                            : "管理员未配置"}
                      </span>
                    </span>
                    <svg
                      className="new-chat-mode__submenu-chevron"
                      viewBox="0 0 12 12"
                      aria-hidden="true"
                    >
                      <path d="m4.5 3 3 3-3 3" />
                    </svg>
                  </button>
                  {sandboxSubmenuOpen ? (
                    <div
                      className="new-chat-mode__submenu"
                      role="group"
                      aria-label="Agent 沙箱"
                    >
                      {SANDBOX_MODES.map((sandboxMode) => (
                        <button
                          key={sandboxMode.value}
                          type="button"
                          role="option"
                          aria-selected={value === sandboxMode.value}
                          aria-disabled={modeDisabled(sandboxMode)}
                          disabled={modeDisabled(sandboxMode)}
                          className="new-chat-mode__option new-chat-mode__submenu-option"
                          onClick={() => choose(sandboxMode)}
                        >
                          <span className="new-chat-mode__option-icon">
                            <ModeIcon mode={sandboxMode.value} />
                          </span>
                          <span className="new-chat-mode__copy">
                            <span className="new-chat-mode__label">{sandboxMode.label}</span>
                            <span>{modeDescription(sandboxMode)}</span>
                          </span>
                          {value === sandboxMode.value ? (
                            <svg
                              className="new-chat-mode__check"
                              viewBox="0 0 16 16"
                              aria-hidden="true"
                            >
                              <path d="m3.5 8.2 2.8 2.8 6.2-6" />
                            </svg>
                          ) : null}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <button
                type="button"
                role="option"
                aria-selected={value === mode.value}
                aria-disabled={modeDisabled(mode)}
                disabled={modeDisabled(mode)}
                className={`new-chat-mode__option${index === activeIndex ? " is-active" : ""}`}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(mode)}
              >
                <span className="new-chat-mode__option-icon"><ModeIcon mode={mode.value} /></span>
                <span className="new-chat-mode__copy">
                  <span className="new-chat-mode__label">
                    {mode.value === "agent" ? agentName : mode.label}
                    {mode.value === "skill-create" ? (
                      <span className="new-chat-mode__beta">Beta</span>
                    ) : null}
                  </span>
                  <span>{modeDescription(mode)}</span>
                </span>
                {value === mode.value ? (
                  <svg className="new-chat-mode__check" viewBox="0 0 16 16" aria-hidden="true">
                    <path d="m3.5 8.2 2.8 2.8 6.2-6" />
                  </svg>
                ) : null}
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
