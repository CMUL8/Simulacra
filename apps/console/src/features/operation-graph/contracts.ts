import type { AsyncState, ReviewDecision } from "../shared";

export type GraphEntityKind = "entity" | "workflow" | "agent" | "approval" | "connector" | "environment";
export interface GraphSummaryItem { id: string; name: string; kind: GraphEntityKind; detail: string; status?: "ready" | "attention" | "draft"; }
export interface GraphRevisionImpact { added: string[]; changed: string[]; removed: string[]; security: string[]; migrations: string[]; tests: string[]; }
export interface GraphComment { id: string; author: string; body: string; createdAt: string; resolved: boolean; mentions?: string[]; section?: string; }
export interface OperationGraphRevision {
  id: string; revision: number; title: string; objective: string; businessSections: Array<{ id: string; title: string; body: string; }>;
  yaml: string; summaries: GraphSummaryItem[]; impact: GraphRevisionImpact; comments: GraphComment[];
  review: { state: "pending" | ReviewDecision; reviewer?: string; note?: string; };
}
export interface OperationGraphAdapter {
  decide(revisionId: string, decision: ReviewDecision, note?: string): Promise<void>;
  addComment(revisionId: string, body: string, section?: string): Promise<GraphComment>;
  resolveComment(commentId: string, resolved: boolean): Promise<void>;
}
export interface OperationGraphProps { revision?: OperationGraphRevision; state?: AsyncState; canReview?: boolean; onRetry?: () => void; adapter?: Partial<OperationGraphAdapter>; }
