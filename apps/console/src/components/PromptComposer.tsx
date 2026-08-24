import { ArrowUp, AtSign, Square } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import type { DataRoomFile } from "../api";
import { userFacingFiles } from "../lib/userFacingFiles";

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
  mentions?: Array<{ id: string; name: string; detail: string; kind: "agent" | "source" }>;
  onMentionSelect?: (id: string, kind: "agent" | "source") => void;
  asTask?: boolean;
  onAsTaskChange?: (value: boolean) => void;
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
  mentions = [],
  onMentionSelect,
  asTask = false,
  onAsTaskChange,
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

  const mentionItems = [
    ...mentions,
    ...userFacingFiles(files).map((file) => ({ id: file.name, name: file.name, detail: file.type, kind: "source" as const })),
  ];
  const filtered = mentionItems.filter((item) =>
    item.name.toLowerCase().includes(mentionFilter.toLowerCase()),
  );

  function insertMention(item: (typeof mentionItems)[number]) {
    const el = areaRef.current;
    if (!el) return;
    const pos = el.selectionStart;
    const before = value.slice(0, pos);
    const atPos = before.lastIndexOf("@");
    const handle = item.kind === "agent" ? item.name.trim().replace(/\s+/g, "_") : item.name;
    const tag = `@${handle} `;
    const next = before.slice(0, atPos) + tag + value.slice(pos);
    onChange(next);
    setMentionOpen(false);
    setMentionFilter("");
    onMentionSelect?.(item.id, item.kind);
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
        insertMention(filtered[mentionIndex]);
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
            <li key={`${f.kind}:${f.id}`}>
              <button
                type="button"
                className={i === mentionIndex ? "active" : ""}
                onMouseDown={(e) => {
                  e.preventDefault();
                  insertMention(f);
                }}
              >
                <span className="mention-tag">@</span>
                {f.name}
                <span className="mention-type">{f.detail}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="composer-inner">
        <button
          type="button"
          className="composer-attach"
          disabled={disabled || busy || mentionItems.length === 0}
          title={mentionItems.length ? "Mention an agent or source" : "No agents or sources"}
          onClick={() => {
            onChange(value + (value && !value.endsWith(" ") ? " @" : "@"));
            areaRef.current?.focus();
            setMentionOpen(true);
            setMentionFilter("");
          }}
        >
          <AtSign size={16} strokeWidth={1.5} />
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
          </span>
          {busy ? (
            <button
              type="button"
              className="send-btn stop"
              onClick={() => onCancel?.()}
              disabled={!onCancel}
              title="Stop"
              aria-label="Stop"
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
              <ArrowUp size={16} strokeWidth={1.5} />
            </button>
          )}
        </div>
      </div>
      {onAsTaskChange ? <label className="composer-task-toggle">
        <input type="checkbox" checked={asTask} onChange={(event) => onAsTaskChange(event.target.checked)} />
        <span>Assign as task</span>
      </label> : null}
    </form>
  );
}
