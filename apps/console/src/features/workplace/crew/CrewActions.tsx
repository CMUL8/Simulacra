import { useRef, useState, type FormEvent } from "react";
import { ChevronDown, Plus, UserPlus, X } from "lucide-react";

import { createCmul8Invitation, createMissionAgent, type MissionAgentInput } from "../../../api";
import "./crew-actions.css";

type MissionRole = "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver";
type AgentDraft = MissionAgentInput;

const emptyAgent: AgentDraft = {
  name: "",
  role: "",
  mandate: "",
  scope: "documents",
  autonomy: "operate_with_checkpoints",
};

const starterJobs: Array<{ label: string; value: AgentDraft }> = [
  { label: "Operations", value: { name: "Operations analyst", role: "Operations specialist", mandate: "Reconcile records, surface exceptions, and prepare exact evidence for human review.", scope: "documents", autonomy: "operate_with_checkpoints" } },
  { label: "Research", value: { name: "Research analyst", role: "Research specialist", mandate: "Ground findings in Mission sources and produce a reviewable, cited brief.", scope: "sources", autonomy: "assist" } },
  { label: "Builder", value: { name: "Product builder", role: "Product builder", mandate: "Turn approved requirements and source data into a working application for human verification.", scope: "app", autonomy: "operate_with_checkpoints" } },
];

function requestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

export function CrewActions({ missionId, canAddAgent, canInviteHuman, onAgentAdded }: {
  missionId: string;
  canAddAgent: boolean;
  canInviteHuman: boolean;
  onAgentAdded: () => void;
}) {
  const [active, setActive] = useState<"agent" | "human" | null>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsTrigger = useRef<HTMLButtonElement>(null);
  const [agent, setAgent] = useState<AgentDraft>(emptyAgent);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<MissionRole>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [invitation, setInvitation] = useState<{ url: string; expiresAt: string } | null>(null);

  const close = () => {
    if (busy) return;
    setActive(null);
    setError("");
    setInvitation(null);
  };

  const addAgent = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await createMissionAgent(missionId, agent);
      setAgent(emptyAgent);
      setActive(null);
      onAgentAdded();
    } catch {
      setError("The agent could not be added. Your Mission is unchanged; try again.");
    } finally {
      setBusy(false);
    }
  };

  const inviteHuman = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await createCmul8Invitation(missionId, { client_request_id: requestId(), email: email.trim(), role });
      const url = new URL(window.location.origin);
      url.searchParams.set("mission_id", missionId);
      url.searchParams.set("invitation_id", created.invitation.id);
      url.searchParams.set("invite_token", created.token);
      setInvitation({ url: url.toString(), expiresAt: created.invitation.expires_at });
      setEmail("");
    } catch {
      setError("The invitation could not be created. No access was changed; try again.");
    } finally {
      setBusy(false);
    }
  };

  return <>
    {canAddAgent || canInviteHuman ? <div className="crew-quick-actions" onKeyDown={(event) => {
      if (event.key !== "Escape") return;
      setActionsOpen(false);
      actionsTrigger.current?.focus();
    }}>
      <button ref={actionsTrigger} className="crew-add-trigger" type="button" aria-expanded={actionsOpen} onClick={() => setActionsOpen((open) => !open)}>
        <Plus size={14} aria-hidden /> Add to crew <ChevronDown size={13} aria-hidden />
      </button>
      {actionsOpen ? <div className="crew-add-options" aria-label="Add to Mission crew">
        {canAddAgent ? <button type="button" onClick={() => { setActionsOpen(false); setActive("agent"); }}><Plus size={14} aria-hidden /> Add agent</button> : null}
        {canInviteHuman ? <button type="button" onClick={() => { setActionsOpen(false); setActive("human"); }}><UserPlus size={14} aria-hidden /> Invite human</button> : null}
      </div> : null}
    </div> : null}

    {active ? <div className="crew-dialog-scrim">
      <form className="crew-dialog" role="dialog" aria-modal="true" aria-label={active === "agent" ? "Add an agent" : "Invite a human"} onSubmit={(event) => void (active === "agent" ? addAgent(event) : inviteHuman(event))}>
        <header>
          <div><p className="workplace-eyebrow">Mission crew</p><h2>{active === "agent" ? "Add an agent" : "Invite a human"}</h2><p>{active === "agent" ? "Define the specialist. Missions manages everything behind the scenes." : "Invite a human to guide, review, or approve work in this Mission."}</p></div>
          <button type="button" aria-label="Close crew dialog" onClick={close}><X size={18} aria-hidden /></button>
        </header>
        {active === "agent" ? <div className="crew-dialog-fields">
          <fieldset className="crew-job-starters"><legend>Start with a job</legend>{starterJobs.map((starter) => <button key={starter.label} type="button" onClick={() => setAgent(starter.value)}>{starter.label}</button>)}</fieldset>
          <label>Name<input autoFocus required placeholder="e.g. Operations analyst" value={agent.name} onChange={(event) => setAgent((current) => ({ ...current, name: event.target.value }))} /></label>
          <label>Job / role<input required placeholder="e.g. Finance operations specialist" value={agent.role} onChange={(event) => setAgent((current) => ({ ...current, role: event.target.value }))} /></label>
          <label className="is-wide">What should this agent own?<textarea required placeholder="Describe the job and what the agent should return to the Mission." value={agent.mandate} onChange={(event) => setAgent((current) => ({ ...current, mandate: event.target.value }))} /></label>
          <label>Scope<select value={agent.scope} onChange={(event) => setAgent((current) => ({ ...current, scope: event.target.value as AgentDraft["scope"] }))}><option value="sources">Mission sources</option><option value="documents">Sources and deliverables</option><option value="app">Sources and the Mission app</option></select></label>
          <label>When should it ask a human?<select value={agent.autonomy} onChange={(event) => setAgent((current) => ({ ...current, autonomy: event.target.value as AgentDraft["autonomy"] }))}><option value="operate_with_checkpoints">Before consequential work</option><option value="assist">Before anything is final</option><option value="execute_safely">Only when outside its scope</option></select></label>
        </div> : invitation ? <div className="crew-invitation-result" role="status"><strong>Invitation ready</strong><p>Share this secure link with the invited human. It expires {new Date(invitation.expiresAt).toLocaleDateString()}.</p><input aria-label="Invitation link" readOnly value={invitation.url} onFocus={(event) => event.currentTarget.select()} /><button type="button" onClick={() => void navigator.clipboard?.writeText(invitation.url)}>Copy link</button></div> : <div className="crew-dialog-fields is-invite">
          <label className="is-wide">Email<input autoFocus required type="email" placeholder="name@company.com" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label className="is-wide">Mission role<select value={role} onChange={(event) => setRole(event.target.value as MissionRole)}><option value="member">Member</option><option value="viewer">Viewer</option><option value="reviewer">Reviewer</option><option value="approver">Approver</option><option value="admin">Admin</option><option value="owner">Owner</option></select></label>
        </div>}
        {error ? <p className="crew-dialog-error" role="alert">{error}</p> : null}
        <footer><button type="button" onClick={close}>Cancel</button>{!invitation ? <button className="is-primary" type="submit" disabled={busy}>{busy ? active === "agent" ? "Adding agent…" : "Creating invitation…" : active === "agent" ? "Add agent" : "Create invitation"}</button> : <button className="is-primary" type="button" onClick={close}>Done</button>}</footer>
      </form>
    </div> : null}
  </>;
}
