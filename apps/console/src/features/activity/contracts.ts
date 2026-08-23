import type { AsyncState } from "../shared";
export type ActivityCategory = "assignment" | "mention" | "review" | "deployment" | "system";
export interface ActivityItem { id: string; category: ActivityCategory; title: string; detail: string; createdAt: string; readAt?: string; actor?: string; project?: string; href?: string; }
export interface AwaySummary { since: string; assignments: number; mentions: number; reviews: number; deployments: number; summary: string; }
export interface ActivityAdapter { markRead(ids: string[]): Promise<void>; open(item: ActivityItem): void; }
export interface ActivityInboxProps { items: ActivityItem[]; awaySummary?: AwaySummary; state?: AsyncState; adapter?: Partial<ActivityAdapter>; onRetry?: () => void; }
