import type { Cmul8CommentRecord, Cmul8DomainEventRecord, Cmul8ReviewRecord, Cmul8RoomPayload, Cmul8TaskRecord } from "../../api";
import type { ActivityItem } from "../activity";
import type { OperationGraphRevision } from "../operation-graph";
import type { ProjectTask, ReviewDecision, TaskStatus, WorkEvent, WorkEventKind } from "../shared";
import type { DurableTaskState, ProjectRoomModel, ProjectRoomPermissions, RoomTask } from "./contracts";

const DURABLE_STATES = new Set<DurableTaskState>(["proposed", "ready", "working", "in_review", "done", "blocked", "failed", "cancelled"]);
const WORK_EVENT_KINDS = new Set<WorkEventKind>(["phase_started", "heartbeat", "handoff", "warning", "completed", "failed", "reconnected"]);

function normalizedStatus(state: DurableTaskState): TaskStatus {
  if (state === "working") return "in_progress";
  if (state === "in_review") return "in_review";
  if (state === "done") return "done";
  if (state === "blocked" || state === "failed") return "blocked";
  return "todo";
}

function durableState(value: string): DurableTaskState {
  return DURABLE_STATES.has(value as DurableTaskState) ? value as DurableTaskState : "proposed";
}

function reviewDecision(value: string): ReviewDecision | undefined {
  if (value === "approve") return "approved";
  if (value === "request_changes" || value === "question" || value === "rollback") return "changes_requested";
  if (value === "reject") return "rejected";
  return undefined;
}

function taskReview(task: Cmul8TaskRecord, reviews: Cmul8ReviewRecord[]): ProjectTask["review"] {
  const latest = reviews.filter((item) => item.task_id === task.id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  const decision = latest ? reviewDecision(latest.decision) : undefined;
	if (latest && decision) return { state: decision, reviewerId: latest.author_id, note: latest.comment, updatedAt: latest.updated_at ?? latest.created_at };
  return { state: task.state === "in_review" ? "requested" : "unrequested" };
}

function mapTask(task: Cmul8TaskRecord, reviews: Cmul8ReviewRecord[]): RoomTask {
  const state = durableState(task.state);
  return { id: task.id, title: task.title, detail: task.objective, status: normalizedStatus(state), durableState: state, ownerId: task.owner_id ?? undefined, review: taskReview(task, reviews), revision: task.revision };
}

function mapGraph(payload: Cmul8RoomPayload): OperationGraphRevision | undefined {
  const plan = payload.mission_plan;
  if (!plan) return undefined;
  const summaries = [
    ...plan.steps.map((name, index) => ({ id: `step-${index}`, name, kind: "workflow" as const, detail: "Mission step" })),
    ...plan.human_checkpoints.map((name, index) => ({ id: `checkpoint-${index}`, name, kind: "approval" as const, detail: "Human checkpoint" })),
  ];
  return {
    id: String(plan.revision), revision: plan.revision,
    title: `Mission plan ${plan.revision}`,
    objective: plan.objective,
    businessSections: [{ id: "scope", title: "Mission scope", body: plan.objective }],
    yaml: "", summaries,
    impact: { added: [], changed: [], removed: [], security: [], migrations: [], tests: [] },
    review: plan.status === "approved" ? { state: "approved" } : { state: "pending" },
    comments: [],
  };
}

function mapWorkEvent(event: Cmul8DomainEventRecord): WorkEvent | null {
  // Detailed work-event payloads are internal records; the room has the
  // product-safe activity stream below instead.
  void event;
  return null;
}

function eventActivity(event: Cmul8DomainEventRecord, readAt?: string): ActivityItem {
  const category = event.action.includes("deploy") ? "deployment" : event.action.includes("review") ? "review" : event.action.includes("claim") || event.action.includes("assign") ? "assignment" : "system";
  const detail = event.result;
  return { id: event.id, category, title: event.action.replaceAll(".", " "), detail, createdAt: event.timestamp, actor: event.actor_id, href: `?roomEvent=${encodeURIComponent(event.id)}`, readAt };
}

function commentActivity(item: Cmul8CommentRecord): ActivityItem {
  return { id: item.id, category: "system", title: "Mission plan comment", detail: item.body, createdAt: item.created_at, actor: item.author_id, href: `?roomComment=${encodeURIComponent(item.id)}` };
}

function reviewActivity(item: Cmul8ReviewRecord): ActivityItem {
	return { id: item.id, category: "review", title: `Task review: ${item.decision.replaceAll("_", " ")}`, detail: item.comment || `Reviewed task ${item.task_id}`, createdAt: item.created_at, actor: item.author_id, href: `?roomTask=${encodeURIComponent(item.task_id)}` };
}

export function mapCmul8RoomPayload(payload: Cmul8RoomPayload, connectionState: ProjectRoomModel["connectionState"] = "connected"): { room: ProjectRoomModel; permissions: ProjectRoomPermissions } {
  // The server intentionally returns only the unread, priority-ranked inbox
  // highlights. Do not infer read state for the rest of the event log.
  const inbox = payload.away.highlights.map((item) => eventActivity(item.event, item.unread ? undefined : item.event.timestamp));
  const activity = [
    ...payload.events.map((event) => eventActivity(event)), ...payload.comments.map(commentActivity), ...payload.reviews.map(reviewActivity),
  ].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  return {
    room: {
      id: payload.room.id, name: payload.project.name, revision: payload.room.revision,
      context: { objective: payload.project.objective, decisions: [], constraints: [] },
      members: payload.room.members.map((member) => {
        const live = payload.presence.find((item) => item.actor_id === member.actor_id);
        return {
        id: member.actor_id, name: member.display_name || member.actor_id, role: member.role,
		kind: member.actor_type === "human" ? "human" : member.actor_type === "agent" ? "agent" : undefined,
		presence: live?.status === "online" ? "active" : live?.status ?? "offline",
		lastSeenAt: live?.last_seen_at ?? undefined,
      }; }),
      tasks: payload.tasks.map((task) => mapTask(task, payload.reviews)), graph: mapGraph(payload),
      workEvents: payload.events.map(mapWorkEvent).filter((event): event is WorkEvent => event !== null),
      connectionState, deployments: [], versions: [], activity, inbox,
      awaySummary: payload.away.since ? {
        since: payload.away.since, assignments: payload.away.counts.assigned ?? 0, mentions: payload.away.counts.mentions ?? 0,
        reviews: (payload.away.counts.reviews ?? 0) + (payload.away.counts.approvals ?? 0), deployments: payload.away.counts.deployments ?? 0,
        summary: `${payload.away.unread} unread of ${payload.away.total} updates`,
      } : undefined,
    },
    permissions: {
		manageTasks: payload.permissions.manage_tasks, reviewTasks: payload.permissions.review_tasks,
		reviewGraph: payload.permissions.review_graph, handoff: false,
		invite: payload.permissions.invite, comment: payload.permissions.comment,
    },
  };
}
