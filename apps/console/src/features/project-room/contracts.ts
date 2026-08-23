import type { ActivityItem, AwaySummary } from "../activity";
import type { GraphComment, OperationGraphRevision } from "../operation-graph";
import type { AsyncState, ConversationContext, DeploymentHealth, MemberPresence, ProjectTask, ReviewDecision, VersionHandoff, WorkEvent } from "../shared";

export type DurableTaskState = "proposed" | "ready" | "working" | "in_review" | "done" | "blocked" | "failed" | "cancelled";
export interface RoomMember { id: string; name: string; role: string; kind?: "human" | "agent"; presence?: MemberPresence; currentTask?: string; lastSeenAt?: string; }
export interface RoomTask extends ProjectTask { durableState: DurableTaskState; revision: number; }
export interface ProjectRoomPermissions { manageTasks: boolean; reviewTasks: boolean; reviewGraph: boolean; handoff: boolean; invite: boolean; comment: boolean; }
export interface ProjectRoomFeatureAdapter {
  transitionTask(taskId: string, state: DurableTaskState, expectedRevision: number): Promise<void>;
  submitTaskReview(taskId: string, decision: ReviewDecision, note: string | undefined, expectedRevision: number): Promise<void>;
  reconnect(): Promise<void>;
  approveGraph(revisionHash: string): Promise<void>;
  addComment(revisionId: string, body: string, section?: string): Promise<GraphComment>;
  selectVersion(versionId: string): Promise<void>;
  handoffVersion(versionId: string, recipientId: string): Promise<void>;
}

export interface ProjectRoomModel {
  id: string; name: string; context: ConversationContext; members: RoomMember[]; tasks: RoomTask[]; graph?: OperationGraphRevision;
  workEvents: WorkEvent[]; connectionState: "connected" | "disconnected" | "unknown"; deployments: DeploymentHealth[]; versions: VersionHandoff[]; selectedVersionId?: string;
  activity?: ActivityItem[]; awaySummary?: AwaySummary;
}
export interface ProjectRoomProps {
  room?: ProjectRoomModel; state?: AsyncState; permissions?: ProjectRoomPermissions;
  adapter?: Partial<ProjectRoomFeatureAdapter>; onRetryLoad?: () => void; onOpenGraph?: () => void; onOpenActivity?: () => void; onInvite?: () => void; actionError?: string;
}
