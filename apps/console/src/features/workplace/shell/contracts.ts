import type { AttentionItem, MissionSummary } from "../../../api";

export type WorkplaceDestination = "missions" | "needs-you" | "work" | "settings";
export type MissionStateFilter = "active" | "all";
export type AttentionFilter = "actionable" | "all";

export type WorkplaceMissionPage = { items: MissionSummary[]; next_cursor: string | null };
export type WorkplaceAttentionPage = {
  items: AttentionItem[];
  next_cursor: string | null;
  unread_count: number;
  actionable_count: number;
};
