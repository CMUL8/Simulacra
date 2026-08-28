import { useState } from "react";
import { CrewRail } from "../workplace/crew/CrewRail";

type MissionRole = "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver";
type InvitationResult = { url: string; expiresAt: string };
type CrewMember = { id: string; name: string; role: string; kind: "human" | "agent"; presence?: "active" | "away" | "offline"; currentTask?: string };

export function TeamRoster({ members, canInvite = false, onInvite, onInviteMember, selectedId, onSelect }: {
  members: CrewMember[];
  canInvite?: boolean;
  onInvite?: () => void;
  onInviteMember?: (email: string, role: MissionRole) => Promise<InvitationResult>;
  selectedId?: string;
  onSelect?: (id: string) => void;
}) {
  void selectedId;
  const [inviteOpen, setInviteOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<MissionRole>("member");
  const [result, setResult] = useState<InvitationResult>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!onInviteMember) return;
    setBusy(true);
    setError("");
    try {
      setResult(await onInviteMember(email.trim(), role));
      setEmail("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Invitation could not be created.");
    } finally {
      setBusy(false);
    }
  }

  const openInvite = canInvite ? () => {
    setInviteOpen((open) => !open);
    setResult(undefined);
    setError("");
    onInvite?.();
  } : undefined;

  return <div className="mission-crew-panel">
    <CrewRail
      agents={members.filter((member) => member.kind === "agent").map((member) => ({ id: member.id, name: member.name, role: member.role, status: member.currentTask ? "working" : "ready", currentWork: member.currentTask }))}
      humans={members.filter((member) => member.kind === "human").map((member) => ({ id: member.id, name: member.name, role: member.role, status: member.presence === "active" ? "online" : member.presence ?? "offline", currentWork: member.currentTask }))}
      canInviteHuman={canInvite}
      onInviteHuman={openInvite}
      onSelect={onSelect ? (member) => onSelect(member.id) : undefined}
    />
    {inviteOpen && onInviteMember ? <form className="mission-crew-invite" onSubmit={(event) => void submit(event)}>
      <header><strong>Invite a human</strong><span>They will join this Mission only.</span></header>
      <label>Email<input autoFocus type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" required /></label>
      <label>Role<select value={role} onChange={(event) => setRole(event.target.value as MissionRole)}>
        <option value="member">Member</option><option value="viewer">Viewer</option><option value="reviewer">Reviewer</option><option value="approver">Approver</option><option value="admin">Admin</option><option value="owner">Owner</option>
      </select></label>
      <button type="submit" disabled={busy}>{busy ? "Creating invitation…" : "Create invitation"}</button>
      {error ? <p role="alert">{error}</p> : null}
      {result ? <div className="mission-crew-invite__result" role="status"><strong>Invitation ready</strong><p>Share this secure link with the invited human. It expires {new Date(result.expiresAt).toLocaleDateString()}.</p><input aria-label="Invitation link" readOnly value={result.url} onFocus={(event) => event.currentTarget.select()} /><button type="button" onClick={() => void navigator.clipboard?.writeText(result.url)}>Copy link</button></div> : null}
    </form> : null}
  </div>;
}
