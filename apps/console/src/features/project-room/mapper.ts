import type { Cmul8CommentRecord, Cmul8DomainEventRecord, Cmul8ReviewRecord, Cmul8RoomPayload, Cmul8TaskRecord } from "../../api";
import type { ActivityItem } from "../activity";
import type { GraphComment, OperationGraphRevision } from "../operation-graph";
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
  if (latest && decision) return { state: decision, reviewerId: latest.reviewer_id, note: latest.body, updatedAt: latest.updated_at ?? latest.created_at };
  return { state: task.state === "in_review" ? "requested" : "unrequested" };
}

function mapTask(task: Cmul8TaskRecord, reviews: Cmul8ReviewRecord[]): RoomTask {
  const state = durableState(task.state);
  return { id: task.id, title: task.title, detail: task.objective, status: normalizedStatus(state), durableState: state, ownerId: task.owner_id ?? undefined, review: taskReview(task, reviews), revision: task.revision };
}

function graphComments(comments: Cmul8CommentRecord[]): GraphComment[] {
  return comments.filter((item) => item.target_type === "graph_element").map((item) => ({
    id: item.id, author: item.author_id, body: item.body, createdAt: item.created_at, resolved: false,
    mentions: item.mentions?.map((mention) => `${mention.ref_type}:${mention.ref_id}`), section: item.graph_path?.replace(/^\/review\//, "") ?? undefined,
  }));
}

function mapGraph(payload: Cmul8RoomPayload): OperationGraphRevision | undefined {
  const revision = payload.operation_graph;
  if (!revision) return undefined;
  const graph = revision.graph;
  const metadata = typeof graph.metadata === "object" && graph.metadata !== null ? graph.metadata as Record<string, unknown> : {};
  const areas = ["entities", "workflows", "agents", "approval_rules", "connectors"] as const;
  const kindByArea = { entities: "entity", workflows: "workflow", agents: "agent", approval_rules: "approval", connectors: "connector" } as const;
  const summaries = areas.flatMap((area) => Array.isArray(graph[area]) ? (graph[area] as Array<Record<string, unknown>>).map((item, index) => ({
    id: typeof item.id === "string" ? item.id : `${area}-${index}`,
    name: typeof item.name === "string" ? item.name : typeof item.id === "string" ? item.id : `${area} ${index + 1}`,
    kind: kindByArea[area], detail: typeof item.description === "string" ? item.description : area.replaceAll("_", " "),
  })) : []);
  const approval = payload.operation_graph_approvals.find((item) => item.revision_hash === revision.revision_hash && item.decision === "approved");
  return {
    id: revision.revision_hash, revision: revision.revision,
    title: typeof metadata.name === "string" ? metadata.name : `Operation Graph revision ${revision.revision}`,
    objective: typeof metadata.description === "string" ? metadata.description : "",
    businessSections: [{ id: "scope", title: "Operational scope", body: areas.map((area) => `${Array.isArray(graph[area]) ? graph[area].length : 0} ${area.replaceAll("_", " ")}`).join(" · ") }],
    yaml: JSON.stringify(graph, null, 2), summaries,
    impact: { added: [], changed: [], removed: [], security: [], migrations: [], tests: [] },
    review: approval ? { state: "approved", reviewer: approval.actor_id } : { state: "pending" },
    comments: graphComments(payload.comments.filter((item) => !item.graph_revision || item.graph_revision === revision.revision_hash)),
  };
}

function mapWorkEvent(event: Cmul8DomainEventRecord): WorkEvent | null {
  const rawKind = event.payload?.work_event_kind;
  if (typeof rawKind !== "string" || !WORK_EVENT_KINDS.has(rawKind as WorkEventKind)) return null;
  const phase = typeof event.payload?.phase === "string" ? event.payload.phase : event.action;
  const message = typeof event.payload?.message === "string" ? event.payload.message : undefined;
  return { id: event.id, kind: rawKind as WorkEventKind, at: event.timestamp, phase, specialist: event.actor_id, message };
}

function eventActivity(event: Cmul8DomainEventRecord): ActivityItem {
  const category = event.action.includes("deploy") ? "deployment" : event.action.includes("review") ? "review" : event.action.includes("claim") || event.action.includes("assign") ? "assignment" : "system";
  const detail = typeof event.payload?.message === "string" ? event.payload.message : event.result;
  return { id: event.id, category, title: event.action.replaceAll(".", " "), detail, createdAt: event.timestamp, actor: event.actor_id, href: `?roomEvent=${encodeURIComponent(event.id)}` };
}

function commentActivity(item: Cmul8CommentRecord): ActivityItem {
  return { id: item.id, category: item.mentions?.length ? "mention" : "system", title: item.target_type === "graph_element" ? "Graph comment" : "Project comment", detail: item.body, createdAt: item.created_at, actor: item.author_id, href: `?roomComment=${encodeURIComponent(item.id)}` };
}

function reviewActivity(item: Cmul8ReviewRecord): ActivityItem {
  return { id: item.id, category: "review", title: `Task review: ${item.decision.replaceAll("_", " ")}`, detail: item.body || `Reviewed task ${item.task_id}`, createdAt: item.created_at, actor: item.reviewer_id, href: `?roomTask=${encodeURIComponent(item.task_id)}` };
}

export function mapCmul8RoomPayload(payload: Cmul8RoomPayload): { room: ProjectRoomModel; permissions: ProjectRoomPermissions } {
  const activity = [
    ...payload.events.map(eventActivity), ...payload.comments.map(commentActivity), ...payload.reviews.map(reviewActivity),
  ].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  return {
    room: {
      id: payload.room.id, name: payload.project.name,
      context: { objective: payload.project.objective, decisions: [], constraints: [] },
      members: payload.room.members.map((member) => {
        const live = payload.presence.find((item) => item.actor_id === member.actor_id);
        return {
        id: member.actor_id, name: member.display_name || member.actor_id, role: member.role,
        kind: member.actor_type === "human" ? "human" : member.actor_type === "builder_agent" || member.actor_type === "runtime_agent" ? "agent" : undefined,
        presence: live?.status ?? (member.presence === "active" || member.presence === "away" || member.presence === "offline" ? member.presence : undefined),
        currentTask: member.current_task, lastSeenAt: live?.last_seen_at ?? member.last_seen_at,
      }; }),
      tasks: payload.tasks.map((task) => mapTask(task, payload.reviews)), graph: mapGraph(payload),
      workEvents: payload.events.map(mapWorkEvent).filter((event): event is WorkEvent => event !== null),
      connectionState: "unknown", deployments: [], versions: [], activity,
      awaySummary: payload.away.since ? {
        since: payload.away.since, assignments: payload.away.counts.assigned ?? 0, mentions: payload.away.counts.mentions ?? 0,
        reviews: (payload.away.counts.reviews ?? 0) + (payload.away.counts.approvals ?? 0), deployments: payload.away.counts.deployments ?? 0,
        summary: `${payload.away.unread} unread of ${payload.away.total} updates`,
      } : undefined,
    },
    permissions: {
      manageTasks: payload.permissions.manage_tasks, reviewTasks: payload.permissions.review_tasks,
      reviewGraph: payload.permissions.review_graph, handoff: false,
      invite: payload.permissions.invite, comment: payload.permissions.manage_tasks,
    },
  };
}
