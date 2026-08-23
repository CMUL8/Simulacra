import { ExternalLink, Send } from "lucide-react";
import { useState } from "react";
import type { ProjectMember, VersionHandoff } from "./contracts";
import "./shared.css";

export function PreviewHandoff({ versions, members, selectedId, disabled, onSelect, onHandoff }: { versions: VersionHandoff[]; members: ProjectMember[]; selectedId?: string; disabled?: boolean; onSelect: (id: string) => void; onHandoff: (versionId: string, recipientId: string) => void }) {
  const [recipient, setRecipient] = useState("");
  const selected = versions.find((version) => version.id === selectedId) ?? versions[0];
  if (!selected) return <p className="cm-inline-empty">No preview versions are available.</p>;
  return (
    <section className="cm-handoff" aria-labelledby="cm-handoff-title">
      <div><span className="cm-eyebrow">Preview handoff</span><h3 id="cm-handoff-title">{selected.label}</h3><p>{selected.summary}</p></div>
      <div className="cm-handoff__controls">
        <label>Version<select value={selected.id} disabled={disabled} onChange={(event) => onSelect(event.target.value)}>{versions.map((version) => <option key={version.id} value={version.id}>{version.label} · {version.state}</option>)}</select></label>
        <label>Recipient<select value={recipient} disabled={disabled} onChange={(event) => setRecipient(event.target.value)}><option value="">Choose teammate</option>{members.map((member) => <option key={member.id} value={member.id}>{member.name} · {member.role}</option>)}</select></label>
        <button type="button" disabled={disabled || !recipient} onClick={() => onHandoff(selected.id, recipient)}><Send size={13} /> Hand off</button>
        {selected.previewUrl ? <a href={selected.previewUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Open preview</a> : null}
      </div>
    </section>
  );
}
