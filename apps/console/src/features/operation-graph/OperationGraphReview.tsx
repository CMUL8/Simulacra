import { Check, ChevronRight, MessageSquare, ShieldAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { FeatureState, type ReviewDecision } from "../shared";
import type { GraphEntityKind, OperationGraphProps } from "./contracts";
import "./operation-graph.css";

const GROUP_LABELS: Record<GraphEntityKind, string> = { entity: "Entities", workflow: "Workflows", agent: "Agents", approval: "Approvals", connector: "Connectors", environment: "Environments" };
const IMPACT_GROUPS = ["added", "changed", "removed", "security", "migrations", "tests"] as const;

export function OperationGraphReview({ revision, state = "ready", canReview = false, onRetry, adapter }: OperationGraphProps) {
  const [tab, setTab] = useState<"business" | "technical" | "impact">("business");
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState(false);
  const groups = useMemo(() => revision ? Object.entries(GROUP_LABELS).map(([kind, label]) => ({ kind: kind as GraphEntityKind, label, items: revision.summaries.filter((item) => item.kind === kind) })) : [], [revision]);
  if (state !== "ready" || !revision) return <FeatureState state={state === "ready" ? "empty" : state} onRetry={onRetry} title={state === "forbidden" ? "Graph review is restricted" : undefined} />;
  async function decide(decision: ReviewDecision) { if (!adapter?.decide) return; setPending(true); try { await adapter.decide(revision!.id, decision, note.trim() || undefined); setNote(""); } finally { setPending(false); } }
  async function submitComment() { if (!adapter?.addComment || !comment.trim()) return; setPending(true); try { await adapter.addComment(revision!.id, comment.trim(), tab); setComment(""); } finally { setPending(false); } }
  return (
    <section className="cm-graph" aria-labelledby="cm-graph-title">
      <header className="cm-graph__header"><div><span className="cm-graph__eyebrow">Operation Graph · revision {revision.revision}</span><h2 id="cm-graph-title">{revision.title}</h2><p>{revision.objective}</p></div><span className={`cm-graph__review cm-graph__review--${revision.review.state}`}>{revision.review.state.replace("_", " ")}</span></header>
      <nav className="cm-graph__tabs" aria-label="Graph review views">{(["business", "technical", "impact"] as const).map((item) => <button type="button" key={item} aria-current={tab === item ? "page" : undefined} onClick={() => setTab(item)}>{item}</button>)}</nav>
      <div className="cm-graph__layout"><div className="cm-graph__main">
        {tab === "business" ? <div className="cm-graph__sections">{revision.businessSections.map((section) => <article key={section.id} id={`graph-${section.id}`}><h3>{section.title}</h3><p>{section.body}</p></article>)}</div> : null}
        {tab === "technical" ? <div><div className="cm-graph__summary" aria-label="Technical graph summary">{groups.map((group) => <section key={group.kind}><h3>{group.label}<span>{group.items.length}</span></h3>{group.items.length ? <ul>{group.items.map((item) => <li key={item.id}><ChevronRight size={12} /><span><strong>{item.name}</strong><small>{item.detail}</small></span><em>{item.status ?? "ready"}</em></li>)}</ul> : <p>None defined</p>}</section>)}</div><pre className="cm-graph__yaml" tabIndex={0} aria-label="Technical graph YAML"><code>{revision.yaml}</code></pre></div> : null}
        {tab === "impact" ? <div className="cm-graph__impact">{IMPACT_GROUPS.map((key) => { const values = revision.impact[key]; return <section key={key}><h3>{key}</h3>{values.length ? <ul>{values.map((value) => <li key={value}>{key === "security" ? <ShieldAlert size={13} /> : <ChevronRight size={13} />}{value}</li>)}</ul> : <p>No impact recorded</p>}</section>; })}</div> : null}
      </div><aside className="cm-graph__aside" aria-label="Review discussion"><h3><MessageSquare size={14} /> Comments <span>{revision.comments.filter((item) => !item.resolved).length}</span></h3>{revision.comments.length ? <ol>{revision.comments.map((item) => <li key={item.id} className={item.resolved ? "is-resolved" : ""}><div><strong>{item.author}</strong><time>{new Date(item.createdAt).toLocaleDateString()}</time></div><p>{item.body}</p>{item.section ? <a href={`#graph-${item.section}`}>{item.section}</a> : null}{adapter?.resolveComment ? <button type="button" onClick={() => adapter.resolveComment?.(item.id, !item.resolved)}>{item.resolved ? "Reopen" : "Resolve"}</button> : null}</li>)}</ol> : <p className="cm-graph__empty">No comments on this revision.</p>}
        {adapter?.addComment ? <form onSubmit={(event) => { event.preventDefault(); void submitComment(); }}><label htmlFor="cm-graph-comment">Add comment or @mention</label><textarea id="cm-graph-comment" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Flag a business or technical concern…" /><button type="submit" disabled={pending || !comment.trim()}>Comment</button></form> : null}
      </aside></div>
      <footer className="cm-graph__decision"><label htmlFor="cm-review-note">Review note <span>required for changes or rejection</span></label><textarea id="cm-review-note" value={note} onChange={(event) => setNote(event.target.value)} disabled={!canReview || pending} placeholder="State the decision rationale and next action" /><div><button type="button" className="is-approve" disabled={!canReview || pending || !adapter?.decide} onClick={() => void decide("approved")}><Check size={14} /> Approve revision</button><button type="button" disabled={!canReview || pending || !adapter?.decide || !note.trim()} onClick={() => void decide("changes_requested")}><MessageSquare size={14} /> Request changes</button><button type="button" className="is-reject" disabled={!canReview || pending || !adapter?.decide || !note.trim()} onClick={() => void decide("rejected")}><X size={14} /> Reject</button></div>{!canReview ? <p role="note">You have comment access. A graph reviewer must make the decision.</p> : null}</footer>
    </section>
  );
}
