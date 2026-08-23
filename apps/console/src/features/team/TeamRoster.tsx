import { Bot, CircleUserRound, UserPlus } from "lucide-react";
import type { ProjectMember } from "../shared";
import "./team.css";

export function TeamRoster({ members, canInvite = false, onInvite, selectedId, onSelect }: { members: ProjectMember[]; canInvite?: boolean; onInvite?: () => void; selectedId?: string; onSelect?: (id: string) => void }) {
  return (
    <section className="cm-team" aria-labelledby="cm-team-title">
      <header><div><span>Coordinated roster</span><h2 id="cm-team-title">Team</h2></div>{canInvite && onInvite ? <button type="button" onClick={onInvite}><UserPlus size={14} /> Invite</button> : null}</header>
      {!members.length ? <p className="cm-team__empty">No teammates have joined this room.</p> : <ul>
        {members.map((member) => <li key={member.id}>
          <button type="button" className={selectedId === member.id ? "is-selected" : ""} aria-pressed={selectedId === member.id} onClick={() => onSelect?.(member.id)} disabled={!onSelect}>
            <span className={`cm-team__presence cm-team__presence--${member.presence}`} aria-label={member.presence} />
            {member.kind === "agent" ? <Bot size={15} aria-hidden="true" /> : <CircleUserRound size={15} aria-hidden="true" />}
            <span className="cm-team__identity"><strong>{member.name}</strong><small>{member.role}</small></span>
            <span className="cm-team__work">{member.currentTask ?? (member.presence === "away" ? "Away" : "Available")}</span>
          </button>
        </li>)}
      </ul>}
    </section>
  );
}
