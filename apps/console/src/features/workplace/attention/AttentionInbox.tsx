import type { AttentionItem } from "../../../api";

const actionLabels: Record<string, string> = {
  update_work: "Update work",
  claim_work: "Claim work",
  decide_checkpoint: "Review decision",
  verify_output: "Verify output",
  approve_plan: "Review plan",
  retry_work: "Retry Mission",
  review_plan: "Review plan",
  open: "Open",
};

function priorityLabel(priority: number): string {
  if (priority <= 10) return "Urgent";
  if (priority <= 20) return "High priority";
  return "Standard priority";
}

export function AttentionInbox({ items, onOpen }: { items: AttentionItem[]; onOpen: (item: AttentionItem) => void }) {
  if (!items.length) return <div className="workplace-empty workplace-empty-state">
    <strong>You are all caught up.</strong>
    <span>When a Mission needs a decision, review, or assignment, it will appear here.</span>
  </div>;
  return <div className="attention-inbox" aria-label="Needs you inbox">
    {items.map((item) => <button className={`attention-row${item.read ? " is-read" : ""}`} key={item.id} onClick={() => onOpen(item)} type="button">
      <span className="attention-row-copy">
        <span className="attention-kicker"><span>{item.read ? "Read" : "Unread"}</span><span>{priorityLabel(item.priority)}</span></span>
        <strong>{item.title}</strong>
        <small>{item.summary}</small>
      </span>
      <span className="attention-row-meta">
        <span className={`attention-action${item.actionable ? " is-actionable" : ""}`}>{item.actionable ? "Action needed" : "For your awareness"}</span>
        <span className="attention-next">{item.allowed_actions.filter((action) => action !== "open").map((action) => actionLabels[action]).find(Boolean) || "Open"}</span>
      </span>
    </button>)}
  </div>;
}
