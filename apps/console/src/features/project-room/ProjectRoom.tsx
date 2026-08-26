import { ArrowRight, CircleUserRound, RefreshCw, ShieldCheck, Users } from "lucide-react";
import { useState } from "react";
import { FeatureState } from "../shared";
import type { ProjectRoomProps, RoomMember } from "./contracts";
import { TaskBoard } from "./TaskBoard";
import "./project-room.css";
import "./room-adapter.css";

const DEFAULT_PERMISSIONS = { manageTasks: false, reviewTasks: false, reviewGraph: false, handoff: false, invite: false, comment: false };
export function ProjectRoom({ room, state = "ready", permissions = DEFAULT_PERMISSIONS, adapter, missionAssignments = [], onRetryLoad, onOpenChat, onInvite, actionError }: ProjectRoomProps) {
  if (state !== "ready" || !room) return <FeatureState state={state === "ready" ? "empty" : state} onRetry={onRetryLoad} title={state === "forbidden" ? "Mission access restricted" : undefined} detail={actionError} />;
  const needsAttention = room.tasks.filter((task) => ["in_review", "blocked", "failed"].includes(task.durableState) || !task.ownerId);
  const working = room.tasks.filter((task) => task.durableState === "working");
  const done = room.tasks.filter((task) => task.durableState === "done");
  const activeAssignments = missionAssignments.filter((item) => !["succeeded", "cancelled"].includes(item.status));
  const planNeedsReview = Boolean(room.graph && room.graph.review.state !== "approved");
  return <main className="cm-room cm-room--tasks" aria-labelledby="cm-room-title"><header className="cm-room__header"><div><span className="cm-room__eyebrow">MISSION WORK</span><h1 id="cm-room-title">Work</h1><p>Assignments, progress, and decisions—one shared queue for humans and agents.</p></div><div className="cm-room__header-actions">{onOpenChat ? <button type="button" onClick={onOpenChat}>Assign work <ArrowRight size={14} /></button> : null}</div></header>
    {actionError ? <div className="cm-room__action-error" role="alert">{actionError}</div> : null}
    {room.connectionState !== "connected" ? <div className={`cm-room__connection cm-room__connection--${room.connectionState}`} role="status"><span>Updates are paused.</span>{adapter?.reconnect ? <button type="button" onClick={() => void adapter.reconnect?.()}><RefreshCw size={13} /> Reconnect</button> : null}</div> : null}
    {planNeedsReview ? <section className="cm-room-plan" aria-label="Mission plan review"><header><span><ShieldCheck size={14} /> BEFORE THE FIRST RUN</span><h2>Approve how the crew will work</h2><p>Confirm the responsibilities and access the Mission prepared from your outcome.</p></header>{room.graph?.summaries.length ? <ul>{room.graph.summaries.slice(0, 4).map((item) => <li key={item.id}><strong>{item.name}</strong><small>{item.detail}</small></li>)}</ul> : null}<div>{permissions.reviewGraph && adapter?.approveGraph ? <button type="button" onClick={() => void adapter.approveGraph?.(room.graph!.id)}>Approve and continue</button> : <span>Waiting for a Mission owner to approve.</span>}</div></section> : null}
    <section className="cm-task-overview" aria-label="Work status"><article className={needsAttention.length || activeAssignments.some((item) => ["awaiting_approval", "failed"].includes(item.status)) ? "attention" : ""}><span>Needs you</span><strong>{needsAttention.length + activeAssignments.filter((item) => ["awaiting_approval", "failed"].includes(item.status)).length}</strong><small>Review, unblock, or assign</small></article><article><span>Working now</span><strong>{working.length + activeAssignments.filter((item) => item.status === "running").length}</strong><small>Humans and agents active</small></article><article><span>Queued</span><strong>{room.tasks.filter((item) => ["proposed", "ready"].includes(item.durableState)).length + activeAssignments.filter((item) => ["queued", "preparing"].includes(item.status)).length}</strong><small>Waiting for the next owner</small></article><article><span>Completed</span><strong>{done.length + missionAssignments.filter((item) => item.status === "succeeded").length}</strong><small>Finished work and evidence</small></article></section>
    <div className="cm-room__columns"><TaskBoard tasks={room.tasks} assignments={missionAssignments} members={room.members} canManage={permissions.manageTasks} canReview={permissions.reviewTasks} onOpenChat={onOpenChat} onClaim={adapter?.claimTask ? (id, revision) => void adapter.claimTask?.(id, revision) : undefined} onTransition={adapter?.transitionTask ? (id, next, revision) => void adapter.transitionTask?.(id, next, revision) : undefined} onReview={adapter?.submitTaskReview ? (id, decision, note, revision) => void adapter.submitTaskReview?.(id, decision, note, revision) : undefined} /></div>
    <details className="cm-room__embedded cm-room__team"><summary><Users size={14} /> Humans in this Mission · {room.members.filter((member) => member.kind !== "agent").length}</summary><RoomRoster members={room.members} canInvite={permissions.invite} onInvite={onInvite} onInviteMember={adapter?.addMember ? (memberId, role) => void adapter.addMember?.(memberId, role, room.revision) : undefined} /></details>
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
  const humans = members.filter((member) => member.kind !== "agent");
  return <section className="cm-room-roster" aria-labelledby="cm-room-roster-title"><header><div><span>MISSION HUMANS</span><h2 id="cm-room-roster-title">Humans who guide and review</h2><p>{humans.length} human{humans.length === 1 ? "" : "s"} in this Mission</p></div>{canInvite && (onInviteMember || onInvite) ? <button type="button" onClick={() => onInviteMember ? setInviteOpen((value) => !value) : onInvite?.()}>Invite human</button> : null}</header>{inviteOpen ? <form className="cm-room-roster__invite" onSubmit={(event) => { event.preventDefault(); invite(); }}><p>Add a human who can steer, collaborate, or review this Mission.</p><label>Email<input autoFocus type="email" value={memberId} onChange={(event) => setMemberId(event.target.value)} placeholder="name@company.com" required /></label><label>Role<select value={role} onChange={(event) => setRole(event.target.value as typeof role)}>{["member", "viewer", "reviewer", "approver", "admin", "owner"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><button type="submit">Add human</button></form> : null}{humans.length ? <ul>{humans.map((member) => <li key={member.id}><CircleUserRound size={14} /><span><strong>{member.name}</strong><small>{member.role}</small></span>{member.presence ? <em>{member.presence}</em> : <em>offline</em>}</li>)}</ul> : <p>No other humans have joined this Mission yet.</p>}</section>;
}
