import type { ActivityAdapter, ActivityItem, AwaySummary } from "../activity";
import type { OperationGraphAdapter, OperationGraphRevision } from "../operation-graph";
import type { AsyncState, ConversationContext, DeploymentHealth, ProjectMember, ProjectRoomAdapter, ProjectTask, VersionHandoff, WorkEvent } from "../shared";

export interface ProjectRoomModel {
  id: string; name: string; context: ConversationContext; members: ProjectMember[]; tasks: ProjectTask[]; graph?: OperationGraphRevision;
  workEvents: WorkEvent[]; connected: boolean; deployments: DeploymentHealth[]; versions: VersionHandoff[]; selectedVersionId?: string;
  activity?: ActivityItem[]; awaySummary?: AwaySummary;
}
export interface ProjectRoomProps {
  room?: ProjectRoomModel; state?: AsyncState; permissions?: { manageTasks: boolean; reviewTasks: boolean; reviewGraph: boolean; handoff: boolean; invite: boolean; };
  adapter?: Partial<ProjectRoomAdapter & OperationGraphAdapter & ActivityAdapter>; onRetryLoad?: () => void; onOpenGraph?: () => void; onOpenActivity?: () => void; onInvite?: () => void;
}
