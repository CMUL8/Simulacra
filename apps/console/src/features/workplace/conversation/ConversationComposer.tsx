import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";

import {
  ApiError,
  postMissionConversationMessage,
  type ConversationSendRequest,
  type ConversationSendResponse,
} from "../../../api";
import { MentionPicker, type MentionChoice } from "./MentionPicker";

export type ConversationAgent = { id: string; name: string; role: string };
export type ConversationHuman = { id: string; display_name: string; role: string };

type FrozenAttempt = {
  payload: ConversationSendRequest;
};

function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

function mentionQuery(value: string): { active: boolean; query: string; start: number } {
  const match = /(^|\s)@([^\s@]*)$/.exec(value);
  if (!match) return { active: false, query: "", start: -1 };
  return { active: true, query: match[2] || "", start: value.length - (match[2]?.length || 0) - 1 };
}

export function ConversationComposer({
  missionId,
  agents,
  humans,
  mentionRequest,
  requestIdFactory = newRequestId,
  send = postMissionConversationMessage,
  onSent,
  onAccessLost,
  assignmentEnabled = true,
  showPlanRecovery = false,
}: {
  missionId: string;
  agents: ConversationAgent[];
  humans: ConversationHuman[];
  mentionRequest?: { key: number; choice: MentionChoice } | null;
  requestIdFactory?: () => string;
  send?: (missionId: string, payload: ConversationSendRequest) => Promise<ConversationSendResponse>;
  onSent: (response: ConversationSendResponse) => void;
  onAccessLost: () => void;
  assignmentEnabled?: boolean;
  showPlanRecovery?: boolean;
}) {
  const [body, setBody] = useState("");
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [humanIds, setHumanIds] = useState<string[]>([]);
  const [manualMessageMode, setManualMessageMode] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [attempt, setAttempt] = useState<FrozenAttempt | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);
  const submittingRef = useRef(false);
  const accessEpoch = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const handledMentionKey = useRef<number | null>(null);

  useEffect(() => () => {
    accessEpoch.current += 1;
    submittingRef.current = false;
  }, [missionId]);

  const query = mentionQuery(body);
  const choices = useMemo<MentionChoice[]>(() => [
    { id: "crew", name: "Crew", detail: "All active Mission agents", kind: "crew" },
    ...agents.map((agent) => ({ id: agent.id, name: agent.name, detail: agent.role, kind: "agent" as const })),
    ...humans.map((human) => ({ id: human.id, name: human.display_name, detail: `Human · ${human.role}`, kind: "human" as const })),
  ], [agents, humans]);

  const choose = useCallback((choice: MentionChoice) => {
    if (attempt) return;
    const found = mentionQuery(body);
    const prefix = found.active ? body.slice(0, found.start) : `${body}${body && !body.endsWith(" ") ? " " : ""}`;
    setBody(`${prefix}@${choice.name} `);
    if (choice.kind === "crew") {
      setAgentIds(agents.map((agent) => agent.id));
      setManualMessageMode(false);
    } else if (choice.kind === "agent") {
      setAgentIds((ids) => ids.includes(choice.id) ? ids : [...ids, choice.id]);
      setManualMessageMode(false);
    } else {
      setHumanIds((ids) => ids.includes(choice.id) ? ids : [...ids, choice.id]);
    }
    setPickerOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [agents, attempt, body]);

  useEffect(() => {
    if (mentionRequest && handledMentionKey.current !== mentionRequest.key) {
      handledMentionKey.current = mentionRequest.key;
      choose(mentionRequest.choice);
    }
  }, [choose, mentionRequest]);

  const assignmentMode = agentIds.length > 0 && !manualMessageMode;
  const selectedAgents = agentIds.map((id) => agents.find((agent) => agent.id === id)).filter((value): value is ConversationAgent => Boolean(value));
  const selectedHumans = humanIds.map((id) => humans.find((human) => human.id === id)).filter((value): value is ConversationHuman => Boolean(value));

  const submitAttempt = async (frozen: FrozenAttempt) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setFailed(false);
    const epoch = accessEpoch.current;
    try {
      const response = await send(missionId, frozen.payload);
      if (accessEpoch.current !== epoch) return;
      onSent(response);
      setAttempt(null);
      setBody("");
      setAgentIds([]);
      setHumanIds([]);
      setManualMessageMode(false);
    } catch (error) {
      if (accessEpoch.current !== epoch) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        accessEpoch.current += 1;
        onAccessLost();
      } else {
        setFailed(true);
      }
    } finally {
      submittingRef.current = false;
      if (accessEpoch.current === epoch) setSubmitting(false);
    }
  };

  const submit = () => {
    const trimmed = body.trim();
    if (!trimmed || attempt || submittingRef.current || (assignmentMode && !assignmentEnabled)) return;
    const mode = assignmentMode ? "assignment" : "message";
    const frozen = {
      payload: {
        client_request_id: requestIdFactory(),
        body: trimmed,
        mode,
        assignee_agent_ids: mode === "assignment" ? [...agentIds] : [],
        reviewer_human_ids: mode === "assignment" ? [...humanIds] : [],
        source_message_id: null,
      },
    } satisfies FrozenAttempt;
    setAttempt(frozen);
    void submitAttempt(frozen);
  };

  return <section className="conversation-composer" aria-label="Mission composer">
    {failed ? <div className="composer-error" role="alert">
      <span>Your work is safe to try again. We will retry safely so it cannot start twice.</span>
      <div className="composer-error-actions">
        <button type="button" className="is-secondary" onClick={() => {
          setAttempt(null);
          setFailed(false);
          window.setTimeout(() => inputRef.current?.focus(), 0);
        }} disabled={submitting}>Edit message</button>
        <button type="button" onClick={() => attempt && void submitAttempt(attempt)} disabled={submitting}>Try again</button>
      </div>
    </div> : null}
    <div className={`composer-command${!agentIds.length && !humanIds.length ? " is-message-only" : ""}`}>
      {assignmentMode || selectedHumans.length ? <div className="composer-routing-preview" aria-live="polite">
        <strong>{assignmentMode ? "Assign work" : "Message"}</strong>
        {assignmentMode ? <span>{selectedAgents.map((agent) => agent.name).join(" → ")}</span> : null}
        {assignmentMode && selectedHumans.length ? <small>{selectedHumans.map((human) => human.display_name).join(", ")} will review</small> : null}
      </div> : null}
      <div className="composer-input-wrap">
        <textarea
          ref={inputRef}
          aria-label="Message the Mission"
          aria-autocomplete="list"
          aria-controls={pickerOpen && query.active ? "mission-mention-picker" : undefined}
          placeholder="Message the Mission or use @ to assign work…"
          value={body}
          disabled={Boolean(attempt)}
          onChange={(event) => {
            setBody(event.currentTarget.value);
            setPickerOpen(mentionQuery(event.currentTarget.value).active);
          }}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
        />
        <MentionPicker choices={choices} query={query.query} active={pickerOpen && query.active} onChoose={choose} onClose={() => setPickerOpen(false)} />
      </div>
      <div className="composer-footer">
        {agentIds.length ? <div className="composer-mode" aria-label="Message mode">
          <button type="button" aria-pressed={!assignmentMode} aria-label="Message mode" onClick={() => setManualMessageMode(true)} disabled={Boolean(attempt)}>Message</button>
          <button type="button" aria-pressed={assignmentMode} aria-label="Assign work mode" onClick={() => setManualMessageMode(false)} disabled={Boolean(attempt)}>Assign work</button>
        </div> : null}
        {(assignmentMode || showPlanRecovery) && !assignmentEnabled ? <a className="composer-plan-link" href={`/missions/${encodeURIComponent(missionId)}?tab=conversation&focus=plan-approval`}>Approve how the crew will work before assigning</a> : null}
        <div className="composer-actions">
          <button
            type="button"
            className={!agentIds.length && !humanIds.length ? "is-icon-only" : undefined}
            aria-label={submitting ? assignmentMode ? "Assigning…" : "Sending…" : assignmentMode ? "Assign work" : "Send message"}
            onClick={submit}
            disabled={!body.trim() || Boolean(attempt) || (assignmentMode && !assignmentEnabled)}
          >
            {!agentIds.length && !humanIds.length
              ? <ArrowUp size={16} aria-hidden="true" />
              : submitting ? assignmentMode ? "Assigning…" : "Sending…" : assignmentMode ? "Assign work" : "Send message"}
          </button>
        </div>
      </div>
    </div>
  </section>;
}
