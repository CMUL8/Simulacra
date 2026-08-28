import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  getMissionConversationReplies,
  postMissionConversationReply,
  type ConversationMessage,
  type ConversationReplyPage,
  type ConversationReplyRequest,
} from "../../../api";

function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

function mergeReplies(current: ConversationMessage[], incoming: ConversationMessage[]): ConversationMessage[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  incoming.forEach((item) => byId.set(item.id, item));
  return [...byId.values()].sort((left, right) => left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id));
}

export function ThreadDrawer({
  missionId,
  root,
  returnFocus,
  onClose,
  onReply,
  onAccessLost,
  requestIdFactory = newRequestId,
  sendReply = postMissionConversationReply,
  loadReplies = getMissionConversationReplies,
}: {
  missionId: string;
  root: ConversationMessage;
  returnFocus: HTMLElement | null;
  onClose: () => void;
  onReply: (reply: ConversationMessage) => void;
  onAccessLost: () => void;
  requestIdFactory?: () => string;
  sendReply?: (missionId: string, messageId: string, payload: ConversationReplyRequest) => Promise<{ message: ConversationMessage }>;
  loadReplies?: (missionId: string, messageId: string, before?: string | null, limit?: number) => Promise<ConversationReplyPage>;
}) {
  const [replies, setReplies] = useState(() => root.thread.latest_replies);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [body, setBody] = useState("");
  const [attempt, setAttempt] = useState<{ payload: ConversationReplyRequest } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const alive = useRef(true);
  const submittingRef = useRef(false);

  const loadLatest = useCallback(async () => {
    setLoading(true);
    setHistoryError(false);
    try {
      const page = await loadReplies(missionId, root.id);
      if (!alive.current) return;
      setReplies(page.items);
      setNextBefore(page.next_before);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        onAccessLost();
      } else if (alive.current) {
        setHistoryError(true);
      }
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [loadReplies, missionId, onAccessLost, root.id]);

  useEffect(() => {
    alive.current = true;
    setReplies(root.thread.latest_replies);
    void loadLatest();
    window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      alive.current = false;
      window.setTimeout(() => returnFocus?.focus(), 0);
    };
  }, [loadLatest, returnFocus, root.id]);

  useEffect(() => {
    setReplies((current) => mergeReplies(current, root.thread.latest_replies));
  }, [root.thread.latest_replies]);

  const replyCount = Math.max(root.thread.reply_count, replies.length);
  const heading = useMemo(() => replyCount === 1 ? "1 reply" : `${replyCount} replies`, [replyCount]);

  const loadOlder = async () => {
    if (!nextBefore || loadingOlder) return;
    setLoadingOlder(true);
    setHistoryError(false);
    try {
      const page = await loadReplies(missionId, root.id, nextBefore);
      if (!alive.current) return;
      setReplies((current) => mergeReplies(page.items, current));
      setNextBefore(page.next_before);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        onAccessLost();
      } else if (alive.current) {
        setHistoryError(true);
      }
    } finally {
      if (alive.current) setLoadingOlder(false);
    }
  };

  const submitAttempt = async (frozen: { payload: ConversationReplyRequest }) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setFailed(false);
    try {
      const response = await sendReply(missionId, root.id, frozen.payload);
      if (!alive.current) return;
      setReplies((current) => mergeReplies(current, [response.message]));
      onReply(response.message);
      setAttempt(null);
      setBody("");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        onAccessLost();
      } else if (alive.current) {
        setFailed(true);
      }
    } finally {
      submittingRef.current = false;
      if (alive.current) setSubmitting(false);
    }
  };

  const submit = () => {
    const clean = body.trim();
    if (!clean || attempt || submittingRef.current) return;
    const frozen = { payload: { client_request_id: requestIdFactory(), body: clean } };
    setAttempt(frozen);
    void submitAttempt(frozen);
  };

  return <aside
    className="thread-drawer"
    role="dialog"
    aria-modal="true"
    aria-label="Thread"
    onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key === "Tab") {
        const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")];
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }}
  >
    <header className="thread-drawer-header">
      <div><p className="workplace-eyebrow">Conversation</p><h3>Thread</h3><span>{heading}</span></div>
      <button type="button" aria-label="Close thread" onClick={onClose}>Close</button>
    </header>
    <div className="thread-drawer-content">
      <article className="thread-root">
        <strong>{root.author.display_name || "Mission teammate"}</strong>
        <p>{root.body || "This message was removed."}</p>
      </article>
      {nextBefore ? <button type="button" className="thread-load-older" disabled={loadingOlder} onClick={() => void loadOlder()}>{loadingOlder ? "Loading earlier replies…" : "Load earlier replies"}</button> : null}
      {loading ? <p className="thread-state" role="status">Loading replies…</p> : null}
      {historyError ? <div className="thread-state is-error" role="alert"><span>Some replies could not be loaded.</span><button type="button" onClick={() => void (nextBefore ? loadOlder() : loadLatest())}>Try again</button></div> : null}
      {!loading && !replies.length ? <p className="thread-state">No replies yet. Continue the conversation here.</p> : null}
      <div className="thread-replies" aria-label="Thread replies">
        {replies.map((reply) => <article className="thread-reply" key={reply.id}>
          <header><strong>{reply.author.display_name || "Mission teammate"}</strong><time dateTime={reply.created_at}>{new Date(reply.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time></header>
          <p>{reply.body || "This reply was removed."}</p>
        </article>)}
      </div>
    </div>
    <section className="thread-composer" aria-label="Thread reply composer">
      <textarea
        ref={inputRef}
        aria-label="Reply in thread"
        placeholder="Reply in this thread…"
        value={body}
        disabled={Boolean(attempt)}
        onChange={(event) => setBody(event.currentTarget.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            submit();
          }
        }}
      />
      {failed ? <div className="thread-reply-error" role="alert">
        <span>Your reply is safe to try again.</span>
        <button type="button" onClick={() => {
          setAttempt(null);
          setFailed(false);
          window.setTimeout(() => inputRef.current?.focus(), 0);
        }}>Edit reply</button>
        <button type="button" onClick={() => attempt && void submitAttempt(attempt)}>Try again</button>
      </div> : null}
      <button className="thread-submit" type="button" disabled={!body.trim() || Boolean(attempt)} onClick={submit}>{submitting ? "Replying…" : "Reply"}</button>
    </section>
  </aside>;
}
