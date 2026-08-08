import { ArrowUp, AtSign, ChevronDown, Square } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import type { DataRoomFile } from "../api";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onCancel?: () => void;
  placeholder?: string;
  disabled?: boolean;
  busy?: boolean;
  files?: DataRoomFile[];
  submitLabel?: string;
  modeTag?: string;
};

export function PromptComposer({
  value,
  onChange,
  onSubmit,
  onCancel,
  placeholder = "Send follow-up",
  disabled,
  busy,
  files = [],
  submitLabel = "Send",
  modeTag = "Plan",
}: Props) {
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);

  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  const filtered = files.filter((f) =>
    f.name.toLowerCase().includes(mentionFilter.toLowerCase()),
  );

  function insertMention(name: string) {
    const el = areaRef.current;
    if (!el) return;
    const pos = el.selectionStart;
    const before = value.slice(0, pos);
    const atPos = before.lastIndexOf("@");
    const tag = `@${name} `;
    const next = before.slice(0, atPos) + tag + value.slice(pos);
    onChange(next);
    setMentionOpen(false);
    setMentionFilter("");
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = atPos + tag.length;
    });
  }

  function trySubmit() {
    if (!busy && !disabled && value.trim()) onSubmit();
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (mentionOpen && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => (i + 1) % filtered.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((i) => (i - 1 + filtered.length) % filtered.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        insertMention(filtered[mentionIndex].name);
        return;
      }
      if (e.key === "Escape") {
        setMentionOpen(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      trySubmit();
    }
  }

  function onInputChange(v: string) {
    onChange(v);
    const el = areaRef.current;
    if (!el) return;
    const pos = el.selectionStart;
    const before = v.slice(0, pos);
    const atPos = before.lastIndexOf("@");
    if (atPos >= 0 && !before.slice(atPos).includes(" ")) {
      setMentionOpen(true);
      setMentionFilter(before.slice(atPos + 1));
      setMentionIndex(0);
    } else {
      setMentionOpen(false);
    }
  }

  return (
    <form
      className="prompt-composer"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        trySubmit();
      }}
    >
      {mentionOpen && filtered.length > 0 && (
        <ul className="mention-menu" role="listbox">
          {filtered.map((f, i) => (
            <li key={f.name}>
              <button
                type="button"
                className={i === mentionIndex ? "active" : ""}
                onMouseDown={(e) => {
                  e.preventDefault();
                  insertMention(f.name);
                }}
              >
                <span className="mention-tag">@</span>
                {f.name}
                <span className="mention-type">{f.type}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="composer-inner">
        <button
          type="button"
          className="composer-attach"
          disabled={disabled || busy || files.length === 0}
          title={files.length ? "Insert @source" : "No sources"}
          onClick={() => {
            onChange(value + (value && !value.endsWith(" ") ? " @" : "@"));
            areaRef.current?.focus();
            setMentionOpen(true);
            setMentionFilter("");
          }}
        >
          <AtSign size={15} strokeWidth={1.75} />
        </button>

        <textarea
          ref={areaRef}
          value={value}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          disabled={disabled || busy}
          rows={1}
        />

        <div className="composer-trailing">
          <span className="model-tag" title={`${modeTag} mode`}>
            {modeTag}
            <ChevronDown size={12} strokeWidth={2} />
          </span>
          {busy ? (
            <button
              type="button"
              className="send-btn stop"
              onClick={() => onCancel?.()}
              disabled={!onCancel}
              title="Stop Prime"
              aria-label="Stop Prime"
            >
              <Square size={11} fill="currentColor" />
            </button>
          ) : (
            <button
              type="submit"
              className="send-btn"
              disabled={disabled || !value.trim()}
              aria-label={submitLabel || "Send"}
            >
              <ArrowUp size={16} strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
