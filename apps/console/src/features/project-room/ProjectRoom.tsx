import { Activity, ArrowRight, Bot, Boxes, CircleUserRound, GitBranch, RefreshCw, Users } from "lucide-react";
import { useState } from "react";
import type { ActivityItem } from "../activity";
import { ActivityInbox } from "../activity";
import { OperationGraphReview } from "../operation-graph";
import { FeatureState, PreviewHandoff, WorkStatus } from "../shared";
import type { ProjectRoomProps, RoomMember } from "./contracts";
import { TaskBoard } from "./TaskBoard";
import "./project-room.css";
import "./room-adapter.css";

const DEFAULT_PERMISSIONS = { manageTasks: false, reviewTasks: false, reviewGraph: false, handoff: false, invite: false, comment: false };
export function ProjectRoom({ room, state = "ready", permissions = DEFAULT_PERMISSIONS, adapter, missionAssignments = [], onRetryLoad, onOpenGraph, onOpenActivity, onOpenChat, onInvite, actionError }: ProjectRoomProps) {
  if (state !== "ready" || !room) return <FeatureState state={state === "ready" ? "empty" : state} onRetry={onRetryLoad} title={state === "forbidden" ? "Mission access restricted" : undefined} detail={actionError} />;
  const needsAttention = room.tasks.filter((task) => ["in_review", "blocked", "failed"].includes(task.durableState) || !task.ownerId);
  const working = room.tasks.filter((task) => task.durableState === "working");
  const done = room.tasks.filter((task) => task.durableState === "done");
  const activeAssignments = missionAssignments.filter((item) => !["succeeded", "cancelled"].includes(item.status));
  return <main className="cm-room cm-room--tasks" aria-labelledby="cm-room-title"><header className="cm-room__header"><div><span className="cm-room__eyebrow">MISSION WORK</span><h1 id="cm-room-title">Work</h1><p>One queue for assignments, active agent work, and decisions that need a person.</p></div><div className="cm-room__header-actions">{onOpenChat ? <button type="button" onClick={onOpenChat}><ArrowRight size={14} /> Assign in Chat</button> : null}</div></header>
    {actionError ? <div className="cm-room__action-error" role="alert">{actionError}</div> : null}
    {room.connectionState !== "connected" ? <div className={`cm-room__connection cm-room__connection--${room.connectionState}`} role="status"><span>{room.connectionState === "disconnected" ? "Live updates disconnected" : "Live connection not observed"}</span>{adapter?.reconnect ? <button type="button" onClick={() => void adapter.reconnect?.()}><RefreshCw size={13} /> Reload room</button> : null}</div> : null}
    <section className="cm-task-overview" aria-label="Work status"><article className={needsAttention.length || activeAssignments.some((item) => ["awaiting_approval", "failed"].includes(item.status)) ? "attention" : ""}><span>Needs you</span><strong>{needsAttention.length + activeAssignments.filter((item) => ["awaiting_approval", "failed"].includes(item.status)).length}</strong><small>Review, unblock, or assign</small></article><article><span>Working now</span><strong>{working.length + activeAssignments.filter((item) => item.status === "running").length}</strong><small>People and agents active</small></article><article><span>Queued</span><strong>{room.tasks.filter((item) => ["proposed", "ready"].includes(item.durableState)).length + activeAssignments.filter((item) => ["queued", "preparing"].includes(item.status)).length}</strong><small>Waiting for the next owner</small></article><article><span>Completed</span><strong>{done.length + missionAssignments.filter((item) => item.status === "succeeded").length}</strong><small>Finished work and evidence</small></article></section>
    <div className="cm-room__columns"><TaskBoard tasks={room.tasks} assignments={missionAssignments} members={room.members} canManage={permissions.manageTasks} canReview={permissions.reviewTasks} onOpenChat={onOpenChat} onClaim={adapter?.claimTask ? (id, revision) => void adapter.claimTask?.(id, revision) : undefined} onTransition={adapter?.transitionTask ? (id, next, revision) => void adapter.transitionTask?.(id, next, revision) : undefined} onReview={adapter?.submitTaskReview ? (id, decision, note, revision) => void adapter.submitTaskReview?.(id, decision, note, revision) : undefined} /></div>
    <details className="cm-room__embedded cm-room__team"><summary><Users size={14} /> Mission team · {room.members.length}</summary><RoomRoster members={room.members} canInvite={permissions.invite} onInvite={onInvite} onInviteMember={adapter?.addMember ? (memberId, role) => void adapter.addMember?.(memberId, role, room.revision) : undefined} /></details>
    <details className="cm-room__embedded cm-room__live"><summary><Activity size={14} /> Live work status</summary><WorkStatus events={room.workEvents} connected={room.connectionState !== "disconnected"} onReconnect={room.connectionState === "disconnected" && adapter?.reconnect ? () => void adapter.reconnect?.() : undefined} /></details>
    <details className="cm-room__embedded"><summary><Boxes size={14} /> Mission context</summary><div className="cm-room__context"><section><h2>Recorded context</h2><dl><div><dt>Decisions</dt><dd>{room.context.decisions.length ? <ul>{room.context.decisions.map((item) => <li key={item}>{item}</li>)}</ul> : "None recorded"}</dd></div><div><dt>Constraints</dt><dd>{room.context.constraints.length ? <ul>{room.context.constraints.map((item) => <li key={item}>{item}</li>)}</ul> : "None recorded"}</dd></div>{room.context.lastHandoff ? <div><dt>Latest handoff</dt><dd>{room.context.lastHandoff}</dd></div> : null}</dl></section></div></details>
    {room.versions.length ? <PreviewHandoff versions={room.versions} members={room.members.map((member) => ({ ...member, kind: member.kind ?? "human", presence: member.presence ?? "offline" }))} selectedId={room.selectedVersionId} disabled={!permissions.handoff || !adapter?.handoffVersion} onSelect={(id) => void adapter?.selectVersion?.(id)} onHandoff={(versionId, recipientId) => void adapter?.handoffVersion?.(versionId, recipientId)} /> : null}
    <section className="cm-room__review-summary" aria-label="Review summary"><div><Boxes size={15} /><span><strong>Mission plan</strong><small>{room.graph ? `Revision ${room.graph.revision} · ${room.graph.review.state.replace("_", " ")}` : "No plan proposed"}</small></span></div>{onOpenGraph ? <button type="button" onClick={onOpenGraph}>Review plan <ArrowRight size={13} /></button> : null}</section>
    <details className="cm-room__embedded"><summary><GitBranch size={14} /> Plan details</summary>{room.graph && permissions.reviewGraph && room.graph.review.state !== "approved" && adapter?.approveGraph ? <div className="cm-room__graph-approve"><span>Approve exact revision <code>{room.graph.id.slice(0, 12)}</code></span><button type="button" onClick={() => void adapter.approveGraph?.(room.graph!.id)}>Approve revision</button></div> : null}<OperationGraphReview revision={room.graph} state={room.graph ? "ready" : "empty"} canReview={false} adapter={permissions.comment && adapter?.addComment ? { addComment: adapter.addComment } : undefined} /></details>
    <details className="cm-room__embedded"><summary><Activity size={14} /> Inbox</summary><ActivityInbox items={room.inbox ?? []} awaySummary={room.awaySummary} adapter={adapter?.markInboxRead ? { markRead: async (ids) => { await adapter.markInboxRead?.(ids.length > 1 ? undefined : ids[0]); } } : undefined} /></details>
    {room.activity ? <details className="cm-room__embedded"><summary><Users size={14} /> Durable activity</summary><RoomActivity items={room.activity} /></details> : null}
  </main>;
}

function RoomRoster({ members, canInvite, onInvite, onInviteMember }: { members: RoomMember[]; canInvite: boolean; onInvite?: () => void; onInviteMember?: (memberId: string, role: "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver") => void }) {
  const [inviteOpen, setInviteOpen] = useState(false);
  const [memberId, setMemberId] = useState("");
  const [role, setRole] = useState<"owner" | "admin" | "member" | "viewer" | "reviewer" | "approver">("member");
  const invite = () => {
    if (!memberId.trim() || !onInviteMember) return;
    onInviteMember(memberId.trim(), role);
    setMemberId("");
    setInviteOpen(false);
  };
  const humans = members.filter((member) => member.kind !== "agent").length;
  const agents = members.filter((member) => member.kind === "agent").length;
  return <section className="cm-room-roster" aria-labelledby="cm-room-roster-title"><header><div><span>MISSION TEAM</span><h2 id="cm-room-roster-title">People & agents</h2><p>{humans} human{humans === 1 ? "" : "s"} · {agents} agent{agents === 1 ? "" : "s"}</p></div>{canInvite && (onInviteMember || onInvite) ? <button type="button" onClick={() => onInviteMember ? setInviteOpen((value) => !value) : onInvite?.()}>Invite human</button> : null}</header>{inviteOpen ? <form className="cm-room-roster__invite" onSubmit={(event) => { event.preventDefault(); invite(); }}><p>Add a teammate who already belongs to this workspace.</p><label>Teammate email<input autoFocus type="email" value={memberId} onChange={(event) => setMemberId(event.target.value)} placeholder="name@company.com" required /></label><label>Mission role<select value={role} onChange={(event) => setRole(event.target.value as typeof role)}>{["member", "viewer", "reviewer", "approver", "admin", "owner"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><button type="submit">Add to Mission</button></form> : null}{members.length ? <ul>{members.map((member) => <li key={member.id}>{member.kind === "agent" ? <Bot size={14} /> : <CircleUserRound size={14} />}<span><strong>{member.name}</strong><small>{member.kind === "agent" ? `Agent · ${member.role}` : `Human · ${member.role}`}</small></span>{member.presence ? <em>{member.presence}</em> : <em>offline</em>}</li>)}</ul> : <p>No teammates have joined this Mission yet.</p>}</section>;
}

function RoomActivity({ items }: { items: ActivityItem[] }) {
  return items.length ? <ol className="cm-room-activity">{items.map((item) => <li key={item.id}><span><strong>{item.title}</strong><small>{item.detail}</small></span><span><em>{item.actor}</em><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time></span></li>)}</ol> : <p className="cm-inline-empty">No durable activity has been recorded.</p>;
}
