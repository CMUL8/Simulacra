import { AtSign, Bell, CheckCheck, GitPullRequest, Rocket, UserRoundCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { FeatureState } from "../shared";
import type { ActivityCategory, ActivityInboxProps } from "./contracts";
import "./activity.css";

const FILTERS: Array<{ id: "all" | "unread" | ActivityCategory; label: string }> = [{ id: "all", label: "All" }, { id: "unread", label: "Unread" }, { id: "assignment", label: "Assignments" }, { id: "mention", label: "Mentions" }, { id: "review", label: "Reviews" }, { id: "deployment", label: "Deployments" }];
const ICONS = { assignment: UserRoundCheck, mention: AtSign, review: GitPullRequest, deployment: Rocket, system: Bell };

export function ActivityInbox({ items, awaySummary, state = "ready", adapter, onRetry }: ActivityInboxProps) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const shown = useMemo(() => items.filter((item) => filter === "all" || filter === "unread" ? filter !== "unread" || !item.readAt : item.category === filter), [items, filter]);
  const unread = items.filter((item) => !item.readAt).length;
  const markRead = adapter?.markRead;
  if (state !== "ready") return <FeatureState state={state} onRetry={onRetry} />;
  return <section className="cm-inbox" aria-labelledby="cm-inbox-title"><header><div><span>Project activity</span><h2 id="cm-inbox-title">Inbox <em aria-label={`${unread} unread`}>{unread}</em></h2></div>{unread && markRead ? <button type="button" onClick={() => void markRead(items.filter((item) => !item.readAt).map((item) => item.id))}><CheckCheck size={14} /> Mark all read</button> : null}</header>
    {awaySummary ? <aside className="cm-inbox__away" aria-label="While you were away"><div><strong>While you were away</strong><time>Since {new Date(awaySummary.since).toLocaleString()}</time></div><p>{awaySummary.summary}</p><dl><div><dt>Assigned</dt><dd>{awaySummary.assignments}</dd></div><div><dt>Mentions</dt><dd>{awaySummary.mentions}</dd></div><div><dt>Reviews</dt><dd>{awaySummary.reviews}</dd></div><div><dt>Deploys</dt><dd>{awaySummary.deployments}</dd></div></dl></aside> : null}
    <nav aria-label="Activity filters">{FILTERS.map((item) => <button type="button" key={item.id} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}</button>)}</nav>
    {!shown.length ? <FeatureState state="empty" title={items.length ? "No matching activity" : "Your inbox is clear"} detail={items.length ? "Choose another filter to see project updates." : "Assignments, mentions, reviews, and deployments will appear here."} /> : <ol className="cm-inbox__list">{shown.map((item) => { const Icon = ICONS[item.category]; return <li key={item.id} className={!item.readAt ? "is-unread" : ""}><button type="button" onClick={() => { if (!item.readAt) void markRead?.([item.id]); if (adapter?.open) adapter.open(item); else if (item.href) window.location.assign(item.href); }} disabled={!adapter?.open && !item.href}><Icon size={15} aria-hidden="true" /><span><strong>{item.title}</strong><small>{item.detail}</small><em>{[item.actor, item.project].filter(Boolean).join(" · ")}</em></span><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time></button></li>})}</ol>}
  </section>;
}
