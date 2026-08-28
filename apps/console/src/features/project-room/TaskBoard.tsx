import { ArrowRight, Check, CircleAlert, Clock3, Eye, UserRound, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReviewDecision } from "../shared";
import type { DurableTaskState, MissionApprovalWork, MissionAssignment, MissionDeliverableWork, RoomMember, RoomTask } from "./contracts";
import "./project-room.css";

type WorkItem =
  | { kind: "room"; id: string; title: string; status: string; owner: string; detail: string; task: RoomTask }
  | { kind: "run"; id: string; title: string; status: string; owner: string; detail: string; assignment: MissionAssignment }
	| { kind: "approval"; id: string; title: string; status: string; owner: string; detail: string; approval: MissionApprovalWork }
	| { kind: "deliverable"; id: string; title: string; status: string; owner: string; detail: string; deliverable: MissionDeliverableWork };

function bucket(item: WorkItem): "needs" | "progress" | "review" | "done" | "closed" {
  if (["cancelled", "expired", "failed", "rejected", "superseded", "closed"].includes(item.status)) return "closed";
  if (["awaiting_approval", "blocked"].includes(item.status) || (item.kind === "room" && !item.task.ownerId)) return "needs";
	if (item.status === "in_review") return "review";
	if (item.status === "ready_for_review") return "review";
  if (["working", "running", "queued", "preparing", "verifying", "ready", "proposed"].includes(item.status)) return "progress";
  if (["done", "succeeded", "verified", "approved", "consumed"].includes(item.status)) return "done";
  return "closed";
}

function nextAction(item: WorkItem): string {
  if (["in_review", "awaiting_approval"].includes(item.status)) return "Your decision is needed";
  if (item.status === "blocked") return "Resolve the blocker";
  if (["failed", "expired"].includes(item.status)) return "Stopped after a problem";
  if (item.kind === "room" && !item.task.ownerId) return "Choose an owner";
  if (["working", "running"].includes(item.status)) return "Work is in progress";
	if (item.status === "verifying") return "Checking the completed work";
  if (["done", "succeeded"].includes(item.status)) return "Finished with evidence";
	if (item.status === "ready_for_review") return "Verify this output";
  if (["cancelled", "rejected", "superseded", "closed"].includes(item.status)) return "Stopped or closed";
  return "Ready to begin";
}

export function TaskBoard({ tasks, assignments = [], approvals = [], deliverables = [], members, canManage, canReview, canDecideMission, onClaim, onTransition, onReview, onDecideApproval, onVerifyDeliverable, onOpenChat }: {
  tasks: RoomTask[]; assignments?: MissionAssignment[]; approvals?: MissionApprovalWork[]; deliverables?: MissionDeliverableWork[]; members: RoomMember[]; canManage: boolean; canReview: boolean; canDecideMission: boolean;
  onClaim?: (taskId: string, expectedRevision: number) => void;
  onTransition?: (taskId: string, state: DurableTaskState, expectedRevision: number) => void;
  onReview?: (taskId: string, decision: ReviewDecision, note: string | undefined, expectedRevision: number) => void;
	onDecideApproval?: (approvalId: string, decision: "approve" | "reject", expectedRevision: number, expectedRunRevision: number) => void;
	onVerifyDeliverable?: (deliverableId: string, expectedVersion: number) => void;
  onOpenChat?: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string>();
  const [note, setNote] = useState("");
  const items = useMemo<WorkItem[]>(() => [
    ...assignments.map((assignment): WorkItem => ({ kind: "run", id: `run:${assignment.id}`, title: assignment.title, status: assignment.status, owner: assignment.currentOwner || (assignment.ownerNames.length ? assignment.ownerNames.join(", ") : "Whole crew"), detail: assignment.currentOwner ? `${assignment.currentOwner} is working now` : "Assigned from the Mission conversation", assignment })),
	...approvals.map((approval): WorkItem => ({ kind: "approval", id: `approval:${approval.id}`, title: approval.title, status: approval.status, owner: "Human decision", detail: approval.detail, approval })),
	...deliverables.map((deliverable): WorkItem => ({ kind: "deliverable", id: `deliverable:${deliverable.id}`, title: deliverable.title, status: deliverable.status, owner: deliverable.status === "done" ? "Verified" : "Human review", detail: deliverable.detail, deliverable })),
    ...tasks.map((task): WorkItem => { const owner = members.find((member) => member.id === task.ownerId); return { kind: "room", id: `task:${task.id}`, title: task.title, status: task.durableState, owner: owner?.name ?? "Unassigned", detail: task.detail || "Mission task", task }; }),
	], [approvals, assignments, deliverables, members, tasks]);
  const selected = items.find((item) => item.id === selectedId);
  const groups = [
    { id: "needs", title: "Needs you", detail: "Review, unblock, or assign", items: items.filter((item) => bucket(item) === "needs") },
    { id: "progress", title: "In progress", detail: "Active or ready for the next owner", items: items.filter((item) => bucket(item) === "progress") },
    { id: "review", title: "Ready for review", detail: "Evidence is ready for a human decision", items: items.filter((item) => bucket(item) === "review") },
    { id: "done", title: "Done", detail: "Finished work and verified results", items: items.filter((item) => bucket(item) === "done") },
	{ id: "closed", title: "Stopped / Closed", detail: "Work that did not finish successfully", items: items.filter((item) => bucket(item) === "closed") },
  ].filter((group) => group.items.length);
  const transition = (task: RoomTask, state: DurableTaskState) => onTransition?.(task.id, state, task.revision);
  const contextualAction = (task: RoomTask) => {
    if (!task.ownerId && task.durableState === "proposed" && canManage && onClaim) return <button className="primary" onClick={() => onClaim(task.id, task.revision)}>Claim task</button>;
    if (task.durableState === "proposed" && canManage) return <button className="primary" onClick={() => transition(task, "ready")}>Queue task</button>;
    if (task.durableState === "ready" && canManage) return <button className="primary" onClick={() => transition(task, "working")}>Start work</button>;
    if (task.durableState === "working" && canManage) return <button className="primary" onClick={() => transition(task, "in_review")}>Send for review</button>;
    if (["blocked", "failed"].includes(task.durableState) && canManage) return <button className="primary" onClick={() => transition(task, "ready")}>Retry from queue</button>;
    return null;
  };
  const approvalAction = (approval: MissionApprovalWork) => approval.status === "awaiting_approval" ? <><button className="approve" disabled={!canDecideMission || !onDecideApproval} onClick={() => onDecideApproval?.(approval.id, "approve", approval.expectedRevision, approval.expectedRunRevision)}>Approve</button><button disabled={!canDecideMission || !onDecideApproval} onClick={() => onDecideApproval?.(approval.id, "reject", approval.expectedRevision, approval.expectedRunRevision)}>Reject</button></> : <p>{approval.status === "done" ? "This approval was recorded and is now part of the Mission history." : "This decision was closed without continuing the Mission."}</p>;
  return <section className="cm-tasks" aria-labelledby="cm-tasks-title"><header><div><span>WORK</span><h2 id="cm-tasks-title">One queue for the whole Mission</h2><p>Assignments, work, review, and results stay together.</p></div><strong>{items.filter((item) => !["done", "closed"].includes(bucket(item))).length} open</strong></header>
    {!items.length ? <div className="cm-tasks__empty"><Check size={18} /><strong>No work assigned yet</strong><span>Go to Chat, mention an agent or @Crew, and send the message as a task.</span>{onOpenChat ? <button type="button" onClick={onOpenChat}>Assign from Chat <ArrowRight size={13} /></button> : null}</div> : <div className="cm-task-groups">{groups.map((group) => <section key={group.id} className={`cm-task-group cm-task-group--${group.id}`}><header><div><h3>{group.title}</h3><p>{group.detail}</p></div><strong>{group.items.length}</strong></header><ol>{group.items.map((item) => <li key={item.id} className={`cm-task cm-task--${item.status}`}><button type="button" className="cm-task__summary" onClick={() => { setSelectedId(item.id); setNote(""); }}><span className="cm-task__state">{bucket(item) === "needs" ? <CircleAlert size={15} /> : bucket(item) === "progress" ? <Clock3 size={15} /> : bucket(item) === "done" ? <Check size={15} /> : <Eye size={15} />}</span><span><span className="cm-task__title-line"><span>{bucket(item) === "closed" ? "stopped / closed" : item.status.replaceAll("_", " ")}</span><small>{nextAction(item)}</small></span><strong>{item.title}</strong><small className="cm-task__detail">{item.detail}</small><span className="cm-task__meta"><span><UserRound size={12} /> {item.owner}</span></span></span><ArrowRight size={14} /></button></li>)}</ol></section>)}</div>}
		{selected ? <div className="cm-work-sheet-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) setSelectedId(undefined); }}><aside className="cm-work-sheet" aria-label="Work details"><header><div><span>{selected.kind === "run" ? "AGENT ASSIGNMENT" : selected.kind === "approval" ? "HUMAN DECISION" : selected.kind === "deliverable" ? "MISSION OUTPUT" : "MISSION TASK"}</span><h2>{selected.title}</h2></div><button type="button" onClick={() => setSelectedId(undefined)} aria-label="Close work details"><X size={17} /></button></header><div className="cm-work-sheet__body"><section className="cm-work-sheet__status"><span>{selected.status.replaceAll("_", " ")}</span><strong>{nextAction(selected)}</strong></section><dl><div><dt>Owner</dt><dd>{selected.owner}</dd></div><div><dt>Purpose</dt><dd>{selected.detail}</dd></div>{selected.kind === "room" && selected.task.blockedBy?.length ? <div><dt>Blocked by</dt><dd>{selected.task.blockedBy.join(", ")}</dd></div> : null}</dl>{selected.kind === "run" ? <section className="cm-work-sheet__handoff"><h3>Where updates return</h3><p>Progress, questions, and completed work return to the Mission conversation where this was assigned.</p>{onOpenChat ? <button type="button" onClick={onOpenChat}>Open conversation</button> : null}</section> : selected.kind === "approval" ? <section className="cm-work-sheet__actions"><h3>Decision</h3>{approvalAction(selected.approval)}</section> : selected.kind === "deliverable" ? <section className="cm-work-sheet__actions"><h3>Review</h3>{selected.deliverable.status === "ready_for_review" ? <button className="approve" disabled={!canReview || !onVerifyDeliverable} onClick={() => onVerifyDeliverable?.(selected.deliverable.id, selected.deliverable.expectedVersion)}>Verify</button> : <p>This output is verified.</p>}</section> : <section className="cm-work-sheet__actions"><h3>Next action</h3>{contextualAction(selected.task)}{selected.task.durableState === "in_review" ? <div className="cm-task__review-actions"><label htmlFor={`task-note-${selected.task.id}`}>Decision note</label><textarea id={`task-note-${selected.task.id}`} value={note} onChange={(event) => setNote(event.target.value)} placeholder="What did you verify, or what needs to change?" disabled={!canReview} /><button className="approve" disabled={!canReview || !onReview} onClick={() => onReview?.(selected.task.id, "approved", note || undefined, selected.task.revision)}>Approve</button><button disabled={!canReview || !onReview || !note.trim()} onClick={() => onReview?.(selected.task.id, "changes_requested", note, selected.task.revision)}>Request changes</button><button disabled={!canReview || !onReview || !note.trim()} onClick={() => onReview?.(selected.task.id, "rejected", note, selected.task.revision)}>Reject</button></div> : null}</section>}</div></aside></div> : null}
  </section>;
}
