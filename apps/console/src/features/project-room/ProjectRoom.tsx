import { Activity, ArrowRight, Boxes, ExternalLink, GitBranch, Server, Users } from "lucide-react";
import { ActivityInbox } from "../activity";
import { OperationGraphReview } from "../operation-graph";
import { FeatureState, PreviewHandoff, WorkStatus } from "../shared";
import { TeamRoster } from "../team";
import type { ProjectRoomProps } from "./contracts";
import { TaskBoard } from "./TaskBoard";
import "./project-room.css";

const DEFAULT_PERMISSIONS = { manageTasks: false, reviewTasks: false, reviewGraph: false, handoff: false, invite: false };
export function ProjectRoom({ room, state = "ready", permissions = DEFAULT_PERMISSIONS, adapter, onRetryLoad, onOpenGraph, onOpenActivity, onInvite }: ProjectRoomProps) {
  if (state !== "ready" || !room) return <FeatureState state={state === "ready" ? "empty" : state} onRetry={onRetryLoad} title={state === "forbidden" ? "Project Room access restricted" : undefined} />;
  return <main className="cm-room" aria-labelledby="cm-room-title"><header className="cm-room__header"><div><span className="cm-room__eyebrow">Project Room</span><h1 id="cm-room-title">{room.name}</h1><p>{room.context.objective}</p></div><div className="cm-room__header-actions">{onOpenActivity ? <button type="button" onClick={onOpenActivity}><Activity size={14} /> Activity</button> : null}{onOpenGraph ? <button type="button" onClick={onOpenGraph}><GitBranch size={14} /> Review graph</button> : null}</div></header>
    <WorkStatus events={room.workEvents} connected={room.connected} onReconnect={adapter?.reconnect ? () => void adapter.reconnect?.() : undefined} onRetry={adapter?.retryWork ? () => void adapter.retryWork?.() : undefined} />
    <div className="cm-room__context"><section><h2>Conversation context</h2><dl><div><dt>Decisions</dt><dd>{room.context.decisions.length ? <ul>{room.context.decisions.map((item) => <li key={item}>{item}</li>)}</ul> : "None recorded"}</dd></div><div><dt>Constraints</dt><dd>{room.context.constraints.length ? <ul>{room.context.constraints.map((item) => <li key={item}>{item}</li>)}</ul> : "None recorded"}</dd></div>{room.context.lastHandoff ? <div><dt>Latest handoff</dt><dd>{room.context.lastHandoff}</dd></div> : null}</dl></section>
      <section className="cm-room__runtime"><h2><Server size={14} /> Runtime health</h2>{room.deployments.length ? <ul>{room.deployments.map((item) => <li key={item.environment}><span className={`cm-health cm-health--${item.state}`} aria-label={item.state} /><strong>{item.environment}</strong><span>{item.version}</span><time>{new Date(item.checkedAt).toLocaleTimeString()}</time>{item.url ? <a href={item.url} target="_blank" rel="noreferrer" aria-label={`Open ${item.environment}`}><ExternalLink size={13} /></a> : null}</li>)}</ul> : <p>No runtime environments connected.</p>}</section></div>
    <div className="cm-room__columns"><TaskBoard tasks={room.tasks} members={room.members} canManage={permissions.manageTasks} canReview={permissions.reviewTasks} onUpdate={adapter?.updateTask ? (id, patch) => void adapter.updateTask?.(id, patch) : undefined} onReview={adapter?.submitTaskReview ? (id, decision, note) => void adapter.submitTaskReview?.(id, decision, note) : undefined} /><TeamRoster members={room.members} canInvite={permissions.invite} onInvite={onInvite} /></div>
    {room.versions.length ? <PreviewHandoff versions={room.versions} members={room.members} selectedId={room.selectedVersionId} disabled={!permissions.handoff || !adapter?.handoffVersion} onSelect={(id) => void adapter?.selectVersion?.(id)} onHandoff={(versionId, recipientId) => void adapter?.handoffVersion?.(versionId, recipientId)} /> : null}
    <section className="cm-room__review-summary" aria-label="Review summary"><div><Boxes size={15} /><span><strong>Operation Graph</strong><small>{room.graph ? `Revision ${room.graph.revision} · ${room.graph.review.state.replace("_", " ")}` : "No revision proposed"}</small></span></div>{onOpenGraph ? <button type="button" onClick={onOpenGraph}>Open review <ArrowRight size={13} /></button> : null}</section>
    <details className="cm-room__embedded"><summary><GitBranch size={14} /> Operation Graph review</summary><OperationGraphReview revision={room.graph} state={room.graph ? "ready" : "empty"} canReview={permissions.reviewGraph} adapter={adapter} /></details>
    {room.activity ? <details className="cm-room__embedded"><summary><Users size={14} /> Room activity</summary><ActivityInbox items={room.activity} awaySummary={room.awaySummary} adapter={adapter} /></details> : null}
  </main>;
}
