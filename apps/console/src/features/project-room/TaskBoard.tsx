import { Check, CircleAlert, Eye, UserRound } from "lucide-react";
import { useState } from "react";
import type { ReviewDecision } from "../shared";
import type { DurableTaskState, RoomMember, RoomTask } from "./contracts";
import "./project-room.css";

const STATUS_LABELS: Record<DurableTaskState, string> = { proposed: "Proposed", ready: "Ready", working: "Working", blocked: "Blocked", in_review: "In review", done: "Done", failed: "Failed", cancelled: "Cancelled" };
const TRANSITIONS: Record<DurableTaskState, DurableTaskState[]> = {
  proposed: ["ready", "cancelled"], ready: ["working", "blocked", "cancelled"], working: ["in_review", "blocked", "failed", "cancelled"],
  in_review: [], blocked: ["ready", "working", "failed", "cancelled"], failed: ["ready", "cancelled"], done: [], cancelled: [],
};

export function TaskBoard({ tasks, members, canManage, canReview, onTransition, onReview }: { tasks: RoomTask[]; members: RoomMember[]; canManage: boolean; canReview: boolean; onTransition?: (taskId: string, state: DurableTaskState, expectedRevision: number) => void; onReview?: (taskId: string, decision: ReviewDecision, note: string | undefined, expectedRevision: number) => void }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (!tasks.length) return <p className="cm-tasks__empty">No tasks yet. Durable assignments will appear here once work is scoped.</p>;
  return <section className="cm-tasks" aria-labelledby="cm-tasks-title"><header><div><span>Durable work queue</span><h2 id="cm-tasks-title">Tasks</h2></div><strong>{tasks.filter((task) => task.status !== "done").length} open</strong></header><ol>{tasks.map((task) => {
    const note = notes[task.id] ?? ""; const owner = members.find((member) => member.id === task.ownerId);
    const transitions = TRANSITIONS[task.durableState];
    return <li key={task.id} className={`cm-task cm-task--${task.durableState}`}><div className="cm-task__summary"><span className="cm-task__state" aria-label={STATUS_LABELS[task.durableState]}>{task.durableState === "blocked" || task.durableState === "failed" ? <CircleAlert size={15} /> : task.durableState === "in_review" ? <Eye size={15} /> : <Check size={15} />}</span><div><h3>{task.title}</h3>{task.detail ? <p>{task.detail}</p> : null}<small>{owner ? `Owned by ${owner.name}` : "Unassigned"}{task.blockedBy?.length ? ` · blocked by ${task.blockedBy.join(", ")}` : ""} · revision {task.revision}</small></div><span className="cm-task__review">{task.review.state.replace("_", " ")}</span></div>
      <div className="cm-task__controls" aria-label={`Controls for ${task.title}`}><span className="cm-task__owner"><UserRound size={12} /> {owner?.name ?? "Unassigned"}</span><label>Status<select aria-label={`Status for ${task.title}`} value={task.durableState} disabled={!canManage || !onTransition || transitions.length === 0} onChange={(event) => onTransition?.(task.id, event.target.value as DurableTaskState, task.revision)}><option value={task.durableState}>{STATUS_LABELS[task.durableState]}</option>{transitions.map((value) => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}</select></label>
      {task.durableState === "in_review" ? <div className="cm-task__review-actions"><label htmlFor={`task-note-${task.id}`}>Review note</label><input id={`task-note-${task.id}`} value={note} onChange={(event) => setNotes((current) => ({ ...current, [task.id]: event.target.value }))} placeholder="Required for changes or rejection" disabled={!canReview} /><button type="button" disabled={!canReview || !onReview} onClick={() => onReview?.(task.id, "approved", note || undefined, task.revision)}>Approve</button><button type="button" disabled={!canReview || !onReview || !note.trim()} onClick={() => onReview?.(task.id, "changes_requested", note, task.revision)}>Request changes</button><button type="button" disabled={!canReview || !onReview || !note.trim()} onClick={() => onReview?.(task.id, "rejected", note, task.revision)}>Reject</button></div> : null}</div>
    </li>; })}</ol></section>;
}
