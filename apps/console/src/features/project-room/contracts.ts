import type { ActivityItem, AwaySummary } from "../activity";
import type { GraphComment, OperationGraphRevision } from "../operation-graph";
import type { AsyncState, ConversationContext, DeploymentHealth, MemberPresence, ProjectTask, ReviewDecision, VersionHandoff, WorkEvent } from "../shared";

export type DurableTaskState = "proposed" | "ready" | "working" | "in_review" | "done" | "blocked" | "failed" | "cancelled";
export interface RoomMember { id: string; name: string; role: string; kind?: "human" | "agent"; presence?: MemberPresence; currentTask?: string; lastSeenAt?: string; }
export interface RoomTask extends ProjectTask { durableState: DurableTaskState; revision: number; }
export interface MissionAssignment { id: string; title: string; status: string; ownerNames: string[]; currentOwner?: string; }
export interface ProjectRoomPermissions { manageTasks: boolean; reviewTasks: boolean; reviewGraph: boolean; handoff: boolean; invite: boolean; comment: boolean; }
export interface ProjectRoomFeatureAdapter {
  addMember(memberId: string, role: "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver", expectedRevision: number): Promise<void>;
  claimTask(taskId: string, expectedRevision: number): Promise<void>;
  transitionTask(taskId: string, state: DurableTaskState, expectedRevision: number): Promise<void>;
  submitTaskReview(taskId: string, decision: ReviewDecision, note: string | undefined, expectedRevision: number): Promise<void>;
  markInboxRead(eventId?: string): Promise<void>;
  reconnect(): Promise<void>;
  approveGraph(revisionHash: string): Promise<void>;
  addComment(revisionId: string, body: string, section?: string): Promise<GraphComment>;
  selectVersion(versionId: string): Promise<void>;
  handoffVersion(versionId: string, recipientId: string): Promise<void>;
}

export interface ProjectRoomModel {
  id: string; name: string; revision: number; context: ConversationContext; members: RoomMember[]; tasks: RoomTask[]; graph?: OperationGraphRevision;
  workEvents: WorkEvent[]; connectionState: "connected" | "disconnected" | "unknown"; deployments: DeploymentHealth[]; versions: VersionHandoff[]; selectedVersionId?: string;
  activity?: ActivityItem[]; inbox?: ActivityItem[]; awaySummary?: AwaySummary;
}
export interface ProjectRoomProps {
  room?: ProjectRoomModel; state?: AsyncState; permissions?: ProjectRoomPermissions;
  missionAssignments?: MissionAssignment[];
  adapter?: Partial<ProjectRoomFeatureAdapter>; onRetryLoad?: () => void; onOpenGraph?: () => void; onOpenActivity?: () => void; onInvite?: () => void; actionError?: string;
}
