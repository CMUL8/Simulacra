import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  getCmul8Room,
  getMission,
  getMissionConversation,
  deleteMissionConversationReaction,
  deleteMissionConversationSaved,
  putMissionConversationReaction,
  putMissionConversationSaved,
  type ConversationMessage,
  type ConversationSendResponse,
  type Cmul8RoomPayload,
  type MissionOverview,
  type WorkspaceEventStream,
} from "../../../api";
import { useMissionConversationLive } from "../shell/useWorkplaceQuery";
import { MissionFiles } from "../files/MissionFiles";
import { WorkList } from "../work/WorkList";
import { CrewActions } from "../crew/CrewActions";
import { ConversationComposer, type ConversationAgent, type ConversationHuman } from "./ConversationComposer";
import { ConversationTimeline } from "./ConversationTimeline";
import type { MentionChoice } from "./MentionPicker";
import { ThreadDrawer } from "./ThreadDrawer";
import "./conversation.css";

function missionTitle(overview: MissionOverview | null, room: Cmul8RoomPayload | null): string {
  const mission = overview?.mission;
  const title = mission && typeof mission.title === "string" ? mission.title.trim() : "";
  const objective = mission && typeof mission.objective === "string" ? mission.objective.trim() : "";
  return title || room?.project.name?.trim() || objective || "Mission";
}

function missionOutcome(overview: MissionOverview | null, room: Cmul8RoomPayload | null): string {
  const mission = overview?.mission;
  const objective = mission && typeof mission.objective === "string" ? mission.objective.trim() : "";
  return objective || room?.project.objective?.trim() || "Humans and agents are carrying this outcome forward together.";
}

function dedupeMessages(items: ConversationMessage[]): ConversationMessage[] {
  const byId = new Map<string, ConversationMessage>();
  items.forEach((item) => byId.set(item.id, item));
  return [...byId.values()].sort((left, right) => left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id));
}

function isAccessLoss(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

const MAX_RECONCILIATION_PAGES = 50;

function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

type MessageActionAttempt = {
  kind: "reaction" | "saved";
  messageId: string;
  reaction: "check" | null;
  next: boolean;
  requestId: string;
  label: string;
  pending: boolean;
  failed: boolean;
};

export function MissionConversationWorkspace({ missionId, currentHumanId, onBack, onView, activeView = "conversation", focusTarget = null, focusAction = null, filesEnabled = false, previewEnabled = false, liveEnabled = false, eventStream }: {
  missionId: string;
  currentHumanId: string;
  onBack: () => void;
  onView?: (view: "conversation" | "work" | "files", focusTarget?: string | null, focusAction?: string | null) => void;
  activeView?: "conversation" | "work" | "files";
  focusTarget?: string | null;
  focusAction?: string | null;
  filesEnabled?: boolean;
  previewEnabled?: boolean;
  liveEnabled?: boolean;
  eventStream?: WorkspaceEventStream;
}) {
  const [overview, setOverview] = useState<MissionOverview | null>(null);
  const [room, setRoom] = useState<Cmul8RoomPayload | null>(null);
  const [crewLoading, setCrewLoading] = useState(true);
  const [crewError, setCrewError] = useState(false);
  const [crewOpen, setCrewOpen] = useState(false);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [conversationLoading, setConversationLoading] = useState(true);
  const [conversationError, setConversationError] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [failedBefore, setFailedBefore] = useState<string | null>(null);
  const olderRequest = useRef(false);
  const activeMission = useRef(missionId);
  activeMission.current = missionId;
  const [mentionRequest, setMentionRequest] = useState<{ key: number; choice: MentionChoice } | null>(null);
  const mentionKey = useRef(0);
  const [threadTarget, setThreadTarget] = useState<{ messageId: string; opener: HTMLButtonElement } | null>(null);
  const [actionAttempt, setActionAttempt] = useState<MessageActionAttempt | null>(null);
  const [historyReloadRequired, setHistoryReloadRequired] = useState(false);
  const [accessLost, setAccessLost] = useState(false);
  const accessLostRef = useRef(false);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const handleAccessLost = useCallback(() => {
    if (accessLostRef.current) return;
    accessLostRef.current = true;
    setAccessLost(true);
    setOverview(null);
    setRoom(null);
    setMessages([]);
    setNextBefore(null);
    setThreadTarget(null);
    setActionAttempt(null);
    setCrewLoading(false);
    setCrewError(false);
    setConversationLoading(false);
    setConversationError(false);
    setLoadingOlder(false);
    setOlderError(null);
    setHistoryReloadRequired(false);
  }, []);

  const loadCrew = useCallback(async () => {
    setCrewLoading(true);
    setCrewError(false);
    const results = await Promise.allSettled([getMission(missionId), getCmul8Room(missionId)]);
    if (activeMission.current !== missionId || accessLostRef.current) return;
    const denied = results.some((result) => result.status === "rejected" && isAccessLoss(result.reason));
    if (denied) {
      handleAccessLost();
      return;
    }
    if (results[0].status === "fulfilled") setOverview(results[0].value);
    if (results[1].status === "fulfilled") setRoom(results[1].value);
    setCrewError(results.every((result) => result.status === "rejected"));
    setCrewLoading(false);
  }, [handleAccessLost, missionId]);

  const loadConversation = useCallback(async () => {
    setConversationLoading(true);
    setConversationError(false);
    try {
      const page = await getMissionConversation(missionId);
      if (activeMission.current !== missionId || accessLostRef.current) return;
      setMessages(dedupeMessages(page.items));
      setNextBefore(page.next_before);
    } catch (error) {
      if (isAccessLoss(error)) {
        handleAccessLost();
      } else if (activeMission.current === missionId && !accessLostRef.current) {
        setConversationError(true);
      }
    } finally {
      if (activeMission.current === missionId && !accessLostRef.current) setConversationLoading(false);
    }
  }, [handleAccessLost, missionId]);

  const refreshConversation = useCallback(async () => {
    const oldestVisible = messagesRef.current[0] || null;
    const pageLimit = 100;
    let before: string | null = null;
    let nextBeforeAfterRefresh: string | null = null;
    let refreshed: ConversationMessage[] = [];
    let foundVisibleAnchor = !oldestVisible;
    let paginationExhausted = false;

    for (let pageNumber = 0; pageNumber < MAX_RECONCILIATION_PAGES; pageNumber += 1) {
      const page = await getMissionConversation(missionId, before, pageLimit);
      refreshed = dedupeMessages([...refreshed, ...page.items]);
      nextBeforeAfterRefresh = page.next_before;
      foundVisibleAnchor = !oldestVisible || refreshed.some((message) => message.id === oldestVisible.id);
      paginationExhausted = !page.next_before;
      if (foundVisibleAnchor || paginationExhausted) break;
      before = page.next_before;
    }

    if (activeMission.current !== missionId || accessLostRef.current) return;
    setMessages(refreshed);
    setNextBefore(nextBeforeAfterRefresh);
    setHistoryReloadRequired(!foundVisibleAnchor && !paginationExhausted);
    setConversationError(false);
  }, [missionId]);

  useMissionConversationLive({
    enabled: activeView === "conversation" && liveEnabled && !accessLost,
    missionId,
    stream: eventStream,
    onRefresh: refreshConversation,
    onAccessLost: handleAccessLost,
  });

  useEffect(() => {
    accessLostRef.current = false;
    setAccessLost(false);
    setOverview(null);
    setRoom(null);
    setMessages([]);
    setNextBefore(null);
    setOlderError(null);
    setFailedBefore(null);
    setThreadTarget(null);
    setActionAttempt(null);
    setHistoryReloadRequired(false);
    void loadCrew();
    if (activeView === "conversation") void loadConversation();
    else setConversationLoading(false);
  }, [activeView, loadConversation, loadCrew]);

  const agents = useMemo<ConversationAgent[]>(() => (overview?.agents || []).map((agent) => ({
    id: agent.id,
    name: agent.name || "Mission agent",
    role: agent.role || "Mission specialist",
  })), [overview]);

  const humans = useMemo<ConversationHuman[]>(() => (room?.room.members || [])
    .filter((member) => member.actor_type !== "agent")
    .map((member) => ({
      id: member.actor_id,
      display_name: member.display_name?.trim() || (member.actor_id === currentHumanId ? "You" : "Mission human"),
      role: member.role,
    })), [currentHumanId, room]);

  const requestMention = (choice: MentionChoice) => {
    mentionKey.current += 1;
    setMentionRequest({ key: mentionKey.current, choice });
  };

  const loadOlder = async (retryBefore?: string | null) => {
    const before = retryBefore || nextBefore;
    if (!before || olderRequest.current) return;
    olderRequest.current = true;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await getMissionConversation(missionId, before);
      if (activeMission.current !== missionId || accessLostRef.current) return;
      setMessages((current) => dedupeMessages([...page.items, ...current]));
      setNextBefore(page.next_before);
      setFailedBefore(null);
      setHistoryReloadRequired(false);
    } catch (error) {
      if (isAccessLoss(error)) {
        handleAccessLost();
      } else if (activeMission.current === missionId && !accessLostRef.current) {
        setFailedBefore(before);
        setOlderError("Could not load earlier messages. Your current conversation is still here.");
      }
    } finally {
      olderRequest.current = false;
      if (activeMission.current === missionId && !accessLostRef.current) setLoadingOlder(false);
    }
  };

  const onSent = (response: ConversationSendResponse) => {
    setMessages((current) => dedupeMessages([...current, response.message]));
    setConversationError(false);
  };

  const replaceMessage = (message: ConversationMessage) => {
    setMessages((current) => dedupeMessages([...current, message]));
  };

  const runAction = async (attempt: MessageActionAttempt) => {
    setActionAttempt({ ...attempt, pending: true, failed: false });
    try {
      if (attempt.kind === "reaction") {
        const response = attempt.next
          ? await putMissionConversationReaction(missionId, attempt.messageId, attempt.reaction || "check", { client_request_id: attempt.requestId })
          : await deleteMissionConversationReaction(missionId, attempt.messageId, attempt.reaction || "check", { client_request_id: attempt.requestId });
        if (activeMission.current === missionId) replaceMessage(response.message);
      } else {
        const response = attempt.next
          ? await putMissionConversationSaved(missionId, attempt.messageId, { client_request_id: attempt.requestId })
          : await deleteMissionConversationSaved(missionId, attempt.messageId, { client_request_id: attempt.requestId });
        if (activeMission.current === missionId) {
          setMessages((current) => current.map((message) => message.id === attempt.messageId ? { ...message, saved: response.saved } : message));
        }
      }
      if (activeMission.current === missionId) setActionAttempt(null);
    } catch (error) {
      if (isAccessLoss(error)) {
        handleAccessLost();
      } else if (activeMission.current === missionId) {
        setActionAttempt({ ...attempt, pending: false, failed: true });
      }
    }
  };

  const selectedThread = threadTarget ? messages.find((message) => message.id === threadTarget.messageId) || null : null;

  const recordThreadReply = (reply: ConversationMessage) => {
    if (!threadTarget) return;
    setMessages((current) => current.map((message) => {
      if (message.id !== threadTarget.messageId) return message;
      const latest = dedupeMessages([...message.thread.latest_replies, reply]).slice(-3);
      return { ...message, thread: { reply_count: Math.max(message.thread.reply_count, latest.length), latest_replies: latest } };
    }));
    void refreshConversation();
  };

  return <section className="mission-conversation-workspace" aria-label="Selected Mission">
    <header className="mission-room-header">
      <button type="button" className="mission-back" onClick={onBack}>Back to Missions</button>
      <div>
        <p className="workplace-eyebrow">Mission</p>
        <h2>{missionTitle(overview, room)}</h2>
        <p>{missionOutcome(overview, room)}</p>
      </div>
    </header>
    <nav className="mission-tabs" aria-label="Mission views">
      {(["conversation", "work", ...(filesEnabled ? ["files"] as const : [])] as const).map((view) => <button
        key={view}
        type="button"
        aria-current={activeView === view ? "page" : undefined}
        onClick={() => onView?.(view)}
      >{view.slice(0, 1).toUpperCase() + view.slice(1)}</button>)}
    </nav>
    {accessLost ? <div className="mission-access-lost" role="alert">
      <strong>You no longer have access to this Mission.</strong>
      <span>Return to Missions to continue with work you can access.</span>
    </div> : <div className="mission-room-layout">
      <aside className={`mission-crew-rail${crewOpen ? " is-open" : ""}`} aria-label="Mission crew">
        <header><div><p className="workplace-eyebrow">Crew</p><h3>Humans and agents</h3></div><span>{agents.length + humans.length}</span><button className="crew-mobile-toggle" type="button" aria-expanded={crewOpen} onClick={() => setCrewOpen((open) => !open)}>{crewOpen ? "Hide" : "Show"}</button></header>
        {crewLoading ? <p className="crew-state" role="status">Loading Mission crew…</p> : null}
        {crewError ? <div className="crew-state" role="alert"><span>Crew is unavailable. This Mission still works.</span><button type="button" onClick={() => void loadCrew()}>Retry</button></div> : null}
        {!crewLoading && !crewError ? <>
          <CrewActions missionId={missionId} canAddAgent={Boolean(room?.permissions.review_graph)} canInviteHuman={Boolean(room?.permissions.invite)} onAgentAdded={() => void loadCrew()} />
          <section aria-label="Agents">
            <h4>Agents</h4>
            {agents.length ? agents.map((agent) => <div className="crew-member" key={agent.id}>
              <span className="crew-avatar is-agent" aria-hidden="true">{agent.name.slice(0, 1).toUpperCase()}</span>
              <span><strong>{agent.name}</strong><small>{agent.role}</small></span>
              <button type="button" aria-label={`Mention ${agent.name}`} onClick={() => requestMention({ id: agent.id, name: agent.name, detail: agent.role, kind: "agent" })}>@</button>
            </div>) : <p className="crew-empty">No agents added yet.</p>}
          </section>
          <section aria-label="Humans">
            <h4>Humans</h4>
            {humans.length ? humans.map((human) => <div className="crew-member" key={human.id}>
              <span className="crew-avatar" aria-hidden="true">{human.display_name.slice(0, 1).toUpperCase()}</span>
              <span><strong>{human.display_name}{human.id === currentHumanId ? " (you)" : ""}</strong><small>{human.role}</small></span>
              <button type="button" aria-label={`Mention ${human.display_name}`} onClick={() => requestMention({ id: human.id, name: human.display_name, detail: `Human · ${human.role}`, kind: "human" })}>@</button>
            </div>) : <p className="crew-empty">No humans are visible yet.</p>}
          </section>
        </> : null}
      </aside>
      {activeView === "conversation" ? <div className="mission-conversation-surface">
        {historyReloadRequired ? <div className="conversation-history-notice" role="status">
          <span>Earlier messages need to be reloaded after reconnecting.</span>
          <button type="button" onClick={() => void loadOlder()}>Reload earlier messages</button>
        </div> : null}
        <ConversationTimeline
          messages={messages}
          loading={conversationLoading}
          error={conversationError ? "Conversation could not be loaded. Your Mission is safe; try again." : null}
          hasOlder={Boolean(nextBefore)}
          loadingOlder={loadingOlder}
          onLoadOlder={() => void loadOlder()}
          olderError={olderError}
          onRetryOlder={() => void loadOlder(failedBefore)}
          focusMessageId={focusTarget === "plan-approval" ? null : focusTarget}
          onReply={(message, opener) => setThreadTarget({ messageId: message.id, opener })}
          onToggleReaction={(message, reaction, next) => {
            if (actionAttempt) return;
            void runAction({ kind: "reaction", messageId: message.id, reaction, next, requestId: newRequestId(), label: "Acknowledgement", pending: false, failed: false });
          }}
          onToggleSaved={(message, next) => {
            if (actionAttempt) return;
            void runAction({ kind: "saved", messageId: message.id, reaction: null, next, requestId: newRequestId(), label: next ? "Save" : "Unsave", pending: false, failed: false });
          }}
          onOpenWork={(workItemId, action) => onView?.("work", workItemId, action)}
          actionAttempt={actionAttempt}
          onRetryAction={() => actionAttempt && void runAction(actionAttempt)}
          onDismissAction={() => setActionAttempt(null)}
        />
        {conversationError && !conversationLoading ? <button className="conversation-retry" type="button" onClick={() => void loadConversation()}>Retry conversation</button> : null}
        <ConversationComposer
          missionId={missionId}
          agents={agents}
          humans={humans}
          mentionRequest={mentionRequest}
          assignmentEnabled={overview?.readiness.graph.status === "approved"}
          showPlanRecovery={focusTarget === "plan-approval"}
          onSent={onSent}
          onAccessLost={handleAccessLost}
        />
      </div> : activeView === "work" ? <div className="mission-secondary-surface"><WorkList missionId={missionId} focusItemId={focusTarget} focusAction={focusAction} onAccessLost={handleAccessLost} /></div> : filesEnabled ? <div className="mission-secondary-surface"><MissionFiles
        missionId={missionId}
        previewEnabled={previewEnabled}
        onAccessLost={handleAccessLost}
        onOpenMessage={(messageId) => onView?.("conversation", messageId)}
      /></div> : null}
      {activeView === "conversation" && selectedThread && threadTarget ? <ThreadDrawer
        missionId={missionId}
        root={selectedThread}
        returnFocus={threadTarget.opener}
        onClose={() => setThreadTarget(null)}
        onReply={recordThreadReply}
        onAccessLost={handleAccessLost}
      /> : null}
    </div>}
  </section>;
}
