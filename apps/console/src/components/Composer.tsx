import { ArrowUp, Bot, Sparkles, User } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef } from "react";
import type { ChatMessage } from "../api";
import { BuildSteps } from "./BuildSteps";
import { Tooltip } from "./ui/Tooltip";

const CHIPS = [
  { label: "Add search", prompt: "Add search to the table" },
  { label: "Tighten hierarchy", prompt: "Tighten the visual hierarchy and spacing" },
  { label: "Stronger title", prompt: "Make the title and subtitle clearer for this topic" },
  { label: "Empty state", prompt: "Improve the empty state copy" },
  { label: "Print polish", prompt: "Make this more print-friendly" },
];

type Props = {
  messages: ChatMessage[];
  input: string;
  busy: boolean;
  disabled?: boolean;
  onInput: (v: string) => void;
  onSend: () => void;
  onChip: (text: string) => void;
};

function formatTime(at?: string) {
  if (!at) return "";
  try {
    return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function Composer({ messages, input, busy, disabled, onInput, onSend, onChip }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (busy) stickToBottom.current = true;
  }, [busy]);

  useEffect(() => {
    if (!stickToBottom.current) return;
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [input]);

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (!busy && input.trim()) onSend();
    }
  }

  return (
    <div className="chat-panel">
      <div
        className="thread"
        ref={threadRef}
        onScroll={() => {
          const el = threadRef.current;
          if (!el) return;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
        }}
      >
        {messages.length === 0 && !busy && (
          <div className="thread-empty">
            <Sparkles size={20} className="thread-empty-icon" />
            <p>Describe the outcome or change. Missions keeps the shared work and preview in sync.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <article key={i} className={`bubble ${m.role}`}>
            <div className="bubble-head">
              <span className="avatar">{m.role === "user" ? <User size={12} /> : <Bot size={12} />}</span>
              <span className="who">{m.role === "user" ? "You" : "Missions"}</span>
              <time>{formatTime(m.at)}</time>
            </div>
            <div className="bubble-body">{m.content}</div>
          </article>
        ))}
        {busy && (
          <article className="bubble assistant">
            <div className="bubble-head">
              <span className="avatar agent"><Bot size={12} /></span>
              <span className="who">Missions</span>
            </div>
            <BuildSteps active />
          </article>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer-wrap">
        <div className="chips">
          {CHIPS.map((c) => (
            <button
              key={c.label}
              type="button"
              className="chip"
              disabled={disabled || busy}
              onClick={() => onChip(c.prompt)}
            >
              {c.label}
            </button>
          ))}
        </div>
        <form
          className="composer"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            onSend();
          }}
        >
          <textarea
            ref={areaRef}
            value={input}
            onChange={(e) => onInput(e.target.value)}
            onKeyDown={onKey}
            placeholder="Ask for changes to your app…"
            disabled={disabled || busy}
            rows={1}
          />
          <div className="composer-bar">
            <span className="model-tag">
              <span className="dot" /> Mission agent
            </span>
            <div className="composer-actions">
              <span className="kbd-hint">⌘↵</span>
              <Tooltip label="Send message (⌘↵)">
                <button type="submit" className="send-btn" disabled={disabled || busy || !input.trim()}>
                  <ArrowUp size={16} strokeWidth={2.5} />
                </button>
              </Tooltip>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
