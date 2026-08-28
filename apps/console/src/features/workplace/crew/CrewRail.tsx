import { Bot, CircleUserRound, Plus, UserPlus } from "lucide-react";

import "./crew.css";

export type CrewAgent = { id: string; name: string; role: string; status: "ready" | "queued" | "working"; currentWork?: string };
export type CrewHuman = { id: string; name: string; role: string; status: "online" | "away" | "offline"; currentWork?: string };

type Props = {
  agents: CrewAgent[];
  humans: CrewHuman[];
  canAddAgent?: boolean;
  canInviteHuman?: boolean;
  onAddAgent?: () => void;
  onInviteHuman?: () => void;
  onSelect?: (member: { kind: "agent" | "human"; id: string }) => void;
};

function label(value: string) { return value[0].toUpperCase() + value.slice(1); }

export function CrewRail({ agents, humans, canAddAgent = false, canInviteHuman = false, onAddAgent, onInviteHuman, onSelect }: Props) {
  return <aside className="crew-rail" aria-label="Mission crew">
    <section aria-labelledby="crew-agents-title"><header><div><span>MISSION CREW</span><h2 id="crew-agents-title">Agents</h2></div>{canAddAgent && onAddAgent ? <button type="button" onClick={onAddAgent}><Plus size={14} /> Add agent</button> : null}</header>
      {agents.length ? <ul>{agents.map((agent) => <li key={agent.id}><button type="button" disabled={!onSelect} onClick={() => onSelect?.({ kind: "agent", id: agent.id })}><Bot size={16} aria-hidden /><span><strong>{agent.name}</strong><small>{agent.role}</small></span><em className={`crew-rail__status crew-rail__status--${agent.status}`}>{agent.currentWork || label(agent.status)}</em></button></li>)}</ul> : <p>No agents yet.</p>}
    </section>
    <section aria-labelledby="crew-humans-title"><header><div><span>GUIDANCE & REVIEW</span><h2 id="crew-humans-title">Humans</h2></div>{canInviteHuman && onInviteHuman ? <button type="button" onClick={onInviteHuman}><UserPlus size={14} /> Invite human</button> : null}</header>
      {humans.length ? <ul>{humans.map((human) => <li key={human.id}><button type="button" disabled={!onSelect} onClick={() => onSelect?.({ kind: "human", id: human.id })}><CircleUserRound size={16} aria-hidden /><span><strong>{human.name}</strong><small>{human.role}</small></span><em className={`crew-rail__status crew-rail__status--${human.status}`}>{human.currentWork || label(human.status)}</em></button></li>)}</ul> : <p>No humans have joined yet.</p>}
    </section>
  </aside>;
}
