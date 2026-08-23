export type AsyncState = "idle" | "loading" | "ready" | "empty" | "error" | "forbidden";

export type MemberPresence = "active" | "away" | "offline";
export type TaskStatus = "todo" | "in_progress" | "blocked" | "in_review" | "done";
export type ReviewDecision = "approved" | "changes_requested" | "rejected";
export type WorkEventKind = "phase_started" | "heartbeat" | "handoff" | "warning" | "completed" | "failed" | "reconnected";

export interface ProjectMember {
  id: string;
  name: string;
  role: string;
  kind: "human" | "agent";
  presence: MemberPresence;
  currentTask?: string;
  lastSeenAt?: string;
}

export interface TaskReview {
  state: "unrequested" | "requested" | ReviewDecision;
  reviewerId?: string;
  note?: string;
  updatedAt?: string;
}

export interface ProjectTask {
  id: string;
  title: string;
  detail?: string;
  status: TaskStatus;
  ownerId?: string;
  dueAt?: string;
  blockedBy?: string[];
  review: TaskReview;
}

export interface WorkEvent {
  id: string;
  kind: WorkEventKind;
  at: string;
  phase: string;
  specialist?: string;
  message?: string;
}

export interface DeploymentHealth {
  environment: string;
  state: "healthy" | "degraded" | "deploying" | "offline";
  version: string;
  checkedAt: string;
  url?: string;
}

export interface ConversationContext {
  objective: string;
  decisions: string[];
  constraints: string[];
  lastHandoff?: string;
}

export interface VersionHandoff {
  id: string;
  label: string;
  createdAt: string;
  createdBy: string;
  summary: string;
  previewUrl?: string;
  state: "draft" | "candidate" | "released" | "superseded";
}

export interface ProjectRoomAdapter {
  updateTask(taskId: string, patch: Pick<ProjectTask, "status" | "ownerId">): Promise<ProjectTask>;
  submitTaskReview(taskId: string, decision: ReviewDecision, note?: string): Promise<ProjectTask>;
  retryWork(): Promise<void>;
  reconnect(): Promise<void>;
  selectVersion(versionId: string): Promise<void>;
  handoffVersion(versionId: string, recipientId: string): Promise<void>;
}

export function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function deriveWorkState(events: WorkEvent[], nowMs: number, stallAfterMs = 45_000) {
  const ordered = [...events].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const first = ordered[0];
  const latest = ordered.at(-1);
  if (!first || !latest) return { state: "idle" as const, elapsedSeconds: 0, phase: undefined, specialist: undefined, lastMessage: undefined };
  const terminal = latest.kind === "completed" || latest.kind === "failed";
  const staleForMs = Math.max(0, nowMs - Date.parse(latest.at));
  const state = latest.kind === "failed" ? "failed" : latest.kind === "completed" ? "completed" : staleForMs >= stallAfterMs ? "stalled" : "running";
  const phaseEvent = [...ordered].reverse().find((event) => event.phase);
  const specialistEvent = [...ordered].reverse().find((event) => event.specialist);
  return {
    state,
    elapsedSeconds: Math.floor(((terminal ? Date.parse(latest.at) : nowMs) - Date.parse(first.at)) / 1000),
    phase: phaseEvent?.phase,
    specialist: specialistEvent?.specialist,
    lastMessage: latest.message,
    lastEventAt: latest.at,
  };
}
