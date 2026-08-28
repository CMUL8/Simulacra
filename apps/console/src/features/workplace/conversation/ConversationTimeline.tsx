import { useLayoutEffect, useMemo, useRef, useState } from "react";

import type { ConversationMessage } from "../../../api";

function dayLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Earlier";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(date);
}

function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
}

const kindLabels: Record<string, string> = {
  assignment_created: "Work assigned",
  agent_started: "Work started",
  agent_progress: "Work update",
  agent_completed: "Work completed",
  human_decision_required: "Human decision needed",
  human_decision_recorded: "Decision recorded",
  output_ready: "Output ready",
  output_verified: "Output verified",
  automation_event: "Scheduled update",
  system_milestone: "Mission update",
};

export function ConversationTimeline({
  messages,
  loading,
  error,
  hasOlder,
  loadingOlder,
  onLoadOlder,
  olderError = null,
  onRetryOlder,
  focusMessageId = null,
  onReply,
  onToggleReaction,
  onToggleSaved,
  actionAttempt = null,
  onRetryAction,
  onDismissAction,
}: {
  messages: ConversationMessage[];
  loading: boolean;
  error: string | null;
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  olderError?: string | null;
  onRetryOlder?: () => void;
  focusMessageId?: string | null;
  onReply?: (message: ConversationMessage, opener: HTMLButtonElement) => void;
  onToggleReaction?: (message: ConversationMessage, reaction: "check", next: boolean) => void;
  onToggleSaved?: (message: ConversationMessage, next: boolean) => void;
  actionAttempt?: { messageId: string; label: string; pending: boolean; failed: boolean } | null;
  onRetryAction?: () => void;
  onDismissAction?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const readingAnchor = useRef<{ id: string; offset: number } | null>(null);
  const previous = useRef<{ count: number; first: string | null; firstAt: string | null; last: string | null; height: number }>({ count: 0, first: null, firstAt: null, last: null, height: 0 });
  const initialPositioned = useRef(false);
  const focusedMessage = useRef<string | null>(null);
  const [newMessageCount, setNewMessageCount] = useState(0);
  const [focusedNewestId, setFocusedNewestId] = useState<string | null>(null);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const first = messages[0]?.id || null;
    const firstAt = messages[0]?.created_at || null;
    const last = messages[messages.length - 1]?.id || null;
    const containerTop = node.getBoundingClientRect().top;
    const anchoredMessage = readingAnchor.current
      ? [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
        .find((candidate) => candidate.dataset.messageId === readingAnchor.current?.id) || null
      : null;
    const anchorDelta = anchoredMessage && readingAnchor.current
      ? anchoredMessage.getBoundingClientRect().top - containerTop - readingAnchor.current.offset
      : 0;
    if (anchoredMessage && anchorDelta) node.scrollTop += anchorDelta;
    const prepended = previous.current.first
      && first !== previous.current.first
      && Boolean(firstAt && previous.current.firstAt && firstAt < previous.current.firstAt);
    const appended = previous.current.last && last !== previous.current.last && !prepended;
    const reconciled = previous.current.first && first !== previous.current.first && !prepended && !appended;
    if (!initialPositioned.current && messages.length) {
      node.scrollTop = node.scrollHeight;
      initialPositioned.current = true;
    } else if (prepended && !anchoredMessage) {
      node.scrollTop += node.scrollHeight - previous.current.height;
    } else if (reconciled && !anchoredMessage) {
      node.scrollTop += node.scrollHeight - previous.current.height;
    } else if (appended && messages.length > previous.current.count) {
      if (nearBottom.current) {
        node.scrollTop = node.scrollHeight;
        setNewMessageCount(0);
      } else {
        setNewMessageCount((count) => count + messages.length - previous.current.count);
      }
    }
    previous.current = { count: messages.length, first, firstAt, last, height: node.scrollHeight };
    if (anchoredMessage && readingAnchor.current) {
      readingAnchor.current = {
        id: readingAnchor.current.id,
        offset: anchoredMessage.getBoundingClientRect().top - node.getBoundingClientRect().top,
      };
    }
  }, [messages]);

  useLayoutEffect(() => {
    if (!focusMessageId || focusedMessage.current === focusMessageId) return;
    const node = scrollRef.current;
    if (!node) return;
    const target = [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
      .find((candidate) => candidate.dataset.messageId === focusMessageId);
    if (!target) return;
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    target.scrollIntoView?.({ block: "center" });
    focusedMessage.current = focusMessageId;
  }, [focusMessageId, messages]);

  useLayoutEffect(() => {
    if (focusedMessage.current !== focusMessageId) focusedMessage.current = null;
  }, [focusMessageId]);

  useLayoutEffect(() => {
    if (!focusedNewestId) return;
    const node = scrollRef.current;
    if (!node) return;
    const target = [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
      .find((candidate) => candidate.dataset.messageId === focusedNewestId);
    target?.focus({ preventScroll: true });
  }, [focusedNewestId, messages]);

  const rows = useMemo(() => {
    let priorDay = "";
    return messages.map((message) => {
      const day = dayLabel(message.created_at);
      const separator = day !== priorDay;
      priorDay = day;
      return { message, day, separator };
    });
  }, [messages]);

  const scrollToNewest = () => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
    nearBottom.current = true;
    setFocusedNewestId(messages[messages.length - 1]?.id || null);
    setNewMessageCount(0);
  };

  const copy = (value: string) => {
    if (navigator.clipboard?.writeText) void navigator.clipboard.writeText(value).catch(() => undefined);
  };

  return <div className="conversation-timeline-frame">
    <div
      className="conversation-timeline"
      ref={scrollRef}
      role="region"
      tabIndex={0}
      aria-label="Mission conversation"
      onScroll={(event) => {
        const node = event.currentTarget;
        nearBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96;
        const containerTop = node.getBoundingClientRect().top;
        const firstVisible = [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
          .find((candidate) => candidate.getBoundingClientRect().bottom > containerTop);
        readingAnchor.current = firstVisible?.dataset.messageId
          ? { id: firstVisible.dataset.messageId, offset: firstVisible.getBoundingClientRect().top - containerTop }
          : null;
        if (nearBottom.current) setNewMessageCount(0);
      }}
    >
    {olderError ? <div className="conversation-history-error" role="alert">
      <span>{olderError}</span>
      <button type="button" onClick={onRetryOlder}>Retry earlier messages</button>
    </div> : hasOlder ? <button className="conversation-load-older" type="button" onClick={onLoadOlder} disabled={loadingOlder}>
      {loadingOlder ? "Loading earlier messages…" : "Load earlier messages"}
    </button> : null}
    {loading ? <div className="conversation-state" role="status">Loading Mission conversation…</div> : null}
    {error ? <div className="conversation-state is-error" role="alert">{error}</div> : null}
    {!loading && !error && !rows.length ? <div className="conversation-state">
      <strong>Start the Mission conversation.</strong>
      <span>Share context or use @ to assign the first piece of work.</span>
    </div> : null}
    {rows.map(({ message, day, separator }) => {
      const acknowledgement = message.reactions.find((reaction) => reaction.reaction === "check");
      const actionBusy = actionAttempt?.messageId === message.id;
      const substantive = Boolean(message.body?.trim());
      return <div key={message.id} className="conversation-message-wrap">
      {separator ? <div className="conversation-day"><span>{day}</span></div> : null}
      <article
        className={`conversation-message kind-${message.kind}`}
        data-message-id={message.id}
        tabIndex={focusedNewestId === message.id ? -1 : undefined}
        aria-label={focusedNewestId === message.id ? `Newest message from ${message.author.display_name || "Mission teammate"}` : undefined}
      >
        <div className="conversation-avatar" aria-hidden="true">{message.author.display_name.trim().slice(0, 1).toUpperCase() || "M"}</div>
        <div className="conversation-message-body">
          <header>
            <strong>{message.author.display_name || "Mission teammate"}</strong>
            {kindLabels[message.kind] ? <span className="conversation-kind">{kindLabels[message.kind]}</span> : null}
            <time dateTime={message.created_at}>{timeLabel(message.created_at)}</time>
          </header>
          <p>{message.body || "This message was removed."}</p>
          {message.links.work_item_id ? <span className="conversation-work-link">Work is tracked in this Mission</span> : null}
          {substantive ? <div className="conversation-message-actions" aria-label={`Actions for ${message.author.display_name || "Mission teammate"}`}>
            <button
              type="button"
              aria-label={`Reply to ${message.author.display_name || "Mission teammate"}`}
              disabled={actionBusy}
              onClick={(event) => onReply?.(message, event.currentTarget)}
            >{message.thread.reply_count ? `Thread · ${message.thread.reply_count}` : "Reply"}</button>
            <button
              type="button"
              aria-pressed={Boolean(acknowledgement?.reacted)}
              aria-label={`${acknowledgement?.reacted ? "Remove acknowledgement" : "Acknowledge"}${acknowledgement?.count ? ` · ${acknowledgement.count}` : ""}`}
              disabled={actionBusy}
              onClick={() => onToggleReaction?.(message, "check", !acknowledgement?.reacted)}
            >✓{acknowledgement?.count ? ` ${acknowledgement.count}` : ""}</button>
            <button
              type="button"
              aria-pressed={message.saved}
              aria-label={message.saved ? "Unsave message" : "Save message"}
              disabled={actionBusy}
              onClick={() => onToggleSaved?.(message, !message.saved)}
            >{message.saved ? "Saved" : "Save"}</button>
            <details className="conversation-message-more">
              <summary>More</summary>
              <div>
                <button type="button" onClick={() => copy(message.body || "")}>Copy message</button>
                <button type="button" onClick={() => copy(`${window.location.origin}/missions/${encodeURIComponent(message.mission_id)}/conversation?focus=${encodeURIComponent(message.id)}`)}>Copy link</button>
              </div>
            </details>
          </div> : null}
          {actionBusy && actionAttempt.failed ? <div className="conversation-action-error" role="alert">
            <span>{actionAttempt.label} was not changed.</span>
            <button type="button" onClick={onDismissAction}>Dismiss</button>
            <button type="button" onClick={onRetryAction}>Try again</button>
          </div> : null}
        </div>
      </article>
    </div>;
    })}
    </div>
    {newMessageCount ? <button
      className="conversation-new-messages"
      type="button"
      aria-label={`${newMessageCount} new message${newMessageCount === 1 ? "" : "s"}`}
      onClick={scrollToNewest}
    >{newMessageCount === 1 ? "New message" : `${newMessageCount} new messages`}</button> : null}
  </div>;
}
