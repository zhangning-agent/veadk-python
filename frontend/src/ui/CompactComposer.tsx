import { useLayoutEffect, useRef } from "react";

import { isImeCompositionEvent } from "./composerKeyboard";
import { ComposerSendIcon, ComposerStopIcon } from "./icons/ComposerIcons";

export interface CompactComposerProps {
  value: string;
  placeholder?: string;
  busy?: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
}

/**
 * The compact form of Studio's conversation composer. It intentionally keeps
 * the same class names, input behaviour, send icon, and focus treatment as the
 * full Composer while omitting Studio-only capability and attachment controls.
 */
export function CompactComposer({
  value,
  placeholder = "输入消息…",
  busy = false,
  disabled = false,
  onChange,
  onSubmit,
  onStop,
}: CompactComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const canSend = Boolean(value.trim()) && !busy && !disabled;

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 144)}px`;
  }, [value]);

  return (
    <form
      className="composer compact-composer"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) onSubmit();
      }}
    >
      <div className="composer-box">
        <div className="composer-input-stack">
          <textarea
            ref={inputRef}
            className="comp-input scroll"
            rows={1}
            value={value}
            disabled={disabled}
            placeholder={placeholder}
            aria-label="输入消息"
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (isImeCompositionEvent(event.nativeEvent)) return;
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (canSend) onSubmit();
              }
            }}
          />
        </div>
        <div className="composer-submit-actions">
          <button
            type={busy && onStop ? "button" : "submit"}
            className="comp-send"
            disabled={disabled || (busy ? !onStop : !canSend)}
            aria-label={busy && onStop ? "停止生成" : busy ? "正在生成" : "发送"}
            onClick={busy && onStop ? onStop : undefined}
          >
            {busy && onStop ? (
              <ComposerStopIcon className="icon" />
            ) : (
              <ComposerSendIcon className="icon" />
            )}
          </button>
        </div>
      </div>
    </form>
  );
}
