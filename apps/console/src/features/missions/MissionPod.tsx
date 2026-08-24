import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot, CalendarClock, Check, ChevronRight, Circle, FileCheck2, FileText,
  Flag, Play, Plus, Settings2, ShieldCheck, Users, X,
} from "lucide-react";
import {
  bootstrapMission, createMissionAgent, createMissionRun, createMissionTrigger,
  getMission, updateMission, verifyMissionDeliverable,
  type MissionAgentInput, type MissionDeliverable, type MissionOverview,
} from "../../api";

const text = (value: unknown) => typeof value === "string" ? value : "";
const pretty = (value: unknown) => text(value).replaceAll("_", " ");
const commaList = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);

type AgentDraft = {
  name: string; role: string; mandate: string; responsibilities: string; dataScope: string;
  tools: string; autonomy: MissionAgentInput["autonomy"]; maxSteps: string; wallTimeout: string;
};

const emptyAgent: AgentDraft = {
  name: "", role: "", mandate: "", responsibilities: "", dataScope: "", tools: "",
  autonomy: "assist", maxSteps: "", wallTimeout: "",
};

export function MissionPod({
  projectId, projectTitle, projectPrompt = "", artifactKind = "report", focus = "summary",
  onClose, onOpenTasks, onOpenFiles,
}: {
  projectId: string;
  projectTitle: string;
  projectPrompt?: string;
  artifactKind?: string;
  focus?: "summary" | "crew";
  onClose: () => void;
  onOpenTasks?: () => void;
  onOpenFiles?: () => void;
}) {
  const [data, setData] = useState<MissionOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agent, setAgent] = useState<AgentDraft>(emptyAgent);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ objective: "", definitionOfDone: "" });
  const [trigger, setTrigger] = useState({ type: "cron", cron: "0 9 * * 1", fact: "", operator: "eq", value: "", timezone: "UTC", policy: "queue" });
  const crewRef = useRef<HTMLElement>(null);

  const refresh = async () => {
    setLoading(true);
    try { setData(await getMission(projectId)); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load Mission"); }
    finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, [projectId]);
  useEffect(() => {
    if (!data?.mission) return;
    setDraft({ objective: text(data.mission.objective), definitionOfDone: text(data.mission.definition_of_done) });
  }, [data?.mission]);
  useEffect(() => {
    if (focus !== "crew" || !data) return;
    window.setTimeout(() => crewRef.current?.scrollIntoView({ block: "start" }), 0);
  }, [focus, data]);

  const mission = data?.mission;
  const objectiveReady = Boolean(text(mission?.objective).trim() && text(mission?.definition_of_done).trim());
  const crewReady = Boolean(data?.agents.length);
  const graphReady = data?.readiness?.graph?.status === "approved";
  const canRun = Boolean(mission && objectiveReady && crewReady && graphReady);
  const activeRun = data?.runs.find((item) => !["completed", "failed", "cancelled"].includes(text(item.status)));
  const pendingOutput = data?.deliverables.filter((item) => text(item.state) !== "verified") ?? [];

  const bootstrap = async () => {
    const deliverable = artifactKind === "data_app" ? "working application" : artifactKind === "slides" ? "slide deck" : artifactKind === "one_pager" ? "one-page brief" : "report";
    try {
      await bootstrapMission(projectId, {
        title: projectTitle,
        objective: projectPrompt.trim() || projectTitle,
        definition_of_done: `Produce a source-grounded ${deliverable}, resolve or flag material exceptions, and obtain human verification of the exact final version.`,
      });
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create Mission"); }
  };

  const run = async () => {
    try { await createMissionRun(projectId); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not start Mission"); }
  };

  const saveMission = async (event: FormEvent) => {
    event.preventDefault();
    if (!mission) return;
    try {
      await updateMission(projectId, { expected_revision: Number(mission.revision), objective: draft.objective, definition_of_done: draft.definitionOfDone });
      setEditing(false);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not update Mission"); }
  };

  const submitAgent = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await createMissionAgent(projectId, {
        name: agent.name,
        role: agent.role,
        mandate: agent.mandate,
        responsibilities: commaList(agent.responsibilities),
        data_scope: commaList(agent.dataScope),
        tools: commaList(agent.tools),
        autonomy: agent.autonomy,
        escalation_actor_id: null,
        budget: {
          ...(agent.maxSteps ? { max_steps: Number(agent.maxSteps) } : {}),
          ...(agent.wallTimeout ? { wall_timeout_seconds: Number(agent.wallTimeout) } : {}),
        },
      });
      setAgent(emptyAgent);
      setAgentOpen(false);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not add agent"); }
  };

  const submitTrigger = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await createMissionTrigger(projectId, trigger.type === "cron"
        ? { type: "cron", cron: trigger.cron, timezone: trigger.timezone, concurrency_policy: trigger.policy as "queue" | "skip" | "replace" | "merge" }
        : { type: "condition", condition: { fact: trigger.fact, operator: trigger.operator, value: trigger.value }, timezone: trigger.timezone, concurrency_policy: trigger.policy as "queue" | "skip" | "replace" | "merge" });
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create automation"); }
  };

  const verify = async (deliverable: MissionDeliverable) => {
    try { await verifyMissionDeliverable(projectId, deliverable.id, deliverable.content_hash, deliverable.revision); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Verification is not permitted"); }
  };

  return <div className="mission-panel-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <aside className="mission-panel" aria-label="Mission details">
      <header className="mission-panel-header"><div><span><Flag size={13} /> MISSION</span><h2>{projectTitle}</h2></div><button type="button" aria-label="Close Mission details" onClick={onClose}><X size={17} /></button></header>
      {error ? <div className="mission-panel-error" role="alert">{error}</div> : null}
      {loading && !data ? <div className="mission-panel-loading">Opening Mission…</div> : !mission ? <div className="mission-panel-bootstrap"><Flag size={22} /><h3>Create the Mission workspace</h3><p>Add a durable outcome, crew, and verification boundary to this project.</p><button onClick={() => void bootstrap()}>Create Mission</button></div> : <div className="mission-panel-scroll">
        <section className="mission-panel-status"><div><span className={`mission-live-dot${activeRun ? " active" : ""}`} /><div><strong>{activeRun ? pretty(activeRun.status) : pretty(mission.status) || "draft"}</strong><small>{activeRun ? "The Mission crew is working" : canRun ? "Ready to run" : "Finish setup to run"}</small></div></div><button disabled={!canRun || Boolean(activeRun)} onClick={() => void run()}><Play size={13} fill="currentColor" /> Run</button></section>

        {!canRun ? <section className="mission-panel-setup"><header><span>SETUP</span><strong>{[objectiveReady, crewReady, graphReady].filter(Boolean).length}/3 complete</strong></header><button className={objectiveReady ? "done" : ""} onClick={() => setEditing(true)}>{objectiveReady ? <Check size={14} /> : <Circle size={14} />}<span><strong>Outcome</strong><small>{objectiveReady ? "Defined" : "Define success"}</small></span><ChevronRight size={14} /></button><button className={crewReady ? "done" : ""} onClick={() => setAgentOpen(true)}>{crewReady ? <Check size={14} /> : <Circle size={14} />}<span><strong>Crew</strong><small>{crewReady ? `${data.agents.length} agents` : "Add a specialist"}</small></span><ChevronRight size={14} /></button><button className={graphReady ? "done" : ""} onClick={onOpenTasks}>{graphReady ? <Check size={14} /> : <Circle size={14} />}<span><strong>Plan</strong><small>{graphReady ? "Approved" : "Review tasks and responsibilities"}</small></span><ChevronRight size={14} /></button></section> : null}

        <section className="mission-panel-section" ref={crewRef}><header><div><span><Users size={14} /> CREW</span><strong>{data.agents.length} agent{data.agents.length === 1 ? "" : "s"}</strong></div><button onClick={() => setAgentOpen(true)}><Plus size={14} /> Agent</button></header>{data.agents.length ? <ul className="mission-panel-crew">{data.agents.map((item) => <li key={text(item.id)}><i>{text(item.name).slice(0, 1).toUpperCase()}</i><span><strong>{text(item.name)}</strong><small>{text(item.role)} · {pretty(item.autonomy)}</small></span><em /></li>)}</ul> : <button className="mission-panel-empty-action" onClick={() => setAgentOpen(true)}><Bot size={18} /><span><strong>Add the first agent</strong><small>Define a durable specialist role.</small></span></button>}</section>

        {pendingOutput.length ? <section className="mission-panel-section"><header><div><span><ShieldCheck size={14} /> NEEDS REVIEW</span><strong>{pendingOutput.length}</strong></div></header><ul className="mission-panel-output">{pendingOutput.map((raw) => { const item = raw as unknown as MissionDeliverable; return <li key={item.id}><FileCheck2 size={16} /><span><strong>{item.name}</strong><small>Version {item.version} · exact output</small></span><button onClick={() => void verify(item)}>Verify</button></li>; })}</ul></section> : null}

        <section className="mission-panel-links"><button onClick={onOpenTasks}><FileCheck2 size={15} /><span><strong>Tasks & approvals</strong><small>Plan, ownership, and decisions</small></span><ChevronRight size={14} /></button><button onClick={onOpenFiles}><FileText size={15} /><span><strong>Mission files</strong><small>Shared sources and evidence</small></span><ChevronRight size={14} /></button></section>

        <details className="mission-panel-details" open={editing}><summary><Settings2 size={14} /> Outcome & settings</summary>{editing ? <form onSubmit={saveMission}><label>Objective<textarea required value={draft.objective} onChange={(event) => setDraft({ ...draft, objective: event.target.value })} /></label><label>Definition of done<textarea required value={draft.definitionOfDone} onChange={(event) => setDraft({ ...draft, definitionOfDone: event.target.value })} /></label><div><button type="button" className="secondary" onClick={() => setEditing(false)}>Cancel</button><button type="submit">Save</button></div></form> : <div className="mission-panel-definition"><p>{text(mission.objective)}</p><span>DONE WHEN</span><p>{text(mission.definition_of_done)}</p><button onClick={() => setEditing(true)}>Edit</button></div>}</details>

        <details className="mission-panel-details"><summary><CalendarClock size={14} /> Automation <em>{data.triggers.length || ""}</em></summary><form className="mission-panel-automation" onSubmit={submitTrigger}><select value={trigger.type} onChange={(event) => setTrigger({ ...trigger, type: event.target.value })}><option value="cron">Schedule</option><option value="condition">Condition</option></select>{trigger.type === "cron" ? <input required aria-label="Cron schedule" value={trigger.cron} onChange={(event) => setTrigger({ ...trigger, cron: event.target.value })} /> : <><input required placeholder="Fact" value={trigger.fact} onChange={(event) => setTrigger({ ...trigger, fact: event.target.value })} /><select value={trigger.operator} onChange={(event) => setTrigger({ ...trigger, operator: event.target.value })}><option value="eq">equals</option><option value="gte">at least</option><option value="contains">contains</option></select><input required placeholder="Value" value={trigger.value} onChange={(event) => setTrigger({ ...trigger, value: event.target.value })} /></>}<div><input aria-label="Timezone" value={trigger.timezone} onChange={(event) => setTrigger({ ...trigger, timezone: event.target.value })} /><select aria-label="Concurrency policy" value={trigger.policy} onChange={(event) => setTrigger({ ...trigger, policy: event.target.value })}><option value="queue">Queue</option><option value="skip">Skip</option><option value="replace">Replace</option><option value="merge">Merge</option></select></div><button><Plus size={13} /> Add automation</button></form></details>
      </div>}
    </aside>

    {agentOpen ? <div className="mission-agent-modal-scrim"><form className="mission-agent-modal" onSubmit={submitAgent}><header><div><span>CREATE AGENT</span><h2>Add a specialist</h2><p>Define the role. Missions manages runtime and model routing.</p></div><button type="button" aria-label="Close agent form" onClick={() => setAgentOpen(false)}><X size={18} /></button></header><div className="mission-agent-fields"><label><span>Name</span><input autoFocus required placeholder="Reconciliation analyst" value={agent.name} onChange={(event) => setAgent({ ...agent, name: event.target.value })} /></label><label><span>Role</span><input required placeholder="Finance operations" value={agent.role} onChange={(event) => setAgent({ ...agent, role: event.target.value })} /></label><label className="wide"><span>Description</span><small>What outcome does this agent own?</small><textarea required placeholder="Reconcile invoices, surface exceptions, and prepare evidence for human review." value={agent.mandate} onChange={(event) => setAgent({ ...agent, mandate: event.target.value })} /></label><label className="wide"><span>Responsibilities</span><input placeholder="match records, investigate exceptions, prepare review pack" value={agent.responsibilities} onChange={(event) => setAgent({ ...agent, responsibilities: event.target.value })} /></label><label><span>Data access</span><input placeholder="invoices/, purchase-orders/" value={agent.dataScope} onChange={(event) => setAgent({ ...agent, dataScope: event.target.value })} /></label><label><span>Capabilities</span><input placeholder="document.read, artifact.write" value={agent.tools} onChange={(event) => setAgent({ ...agent, tools: event.target.value })} /></label><label className="wide"><span>Autonomy</span><select value={agent.autonomy} onChange={(event) => setAgent({ ...agent, autonomy: event.target.value as MissionAgentInput["autonomy"] })}><option value="assist">Assist — draft for a human</option><option value="execute_safely">Execute safely — act within scope</option><option value="operate_with_checkpoints">Checkpoints — ask before consequential work</option></select></label><label><span>Tool limit</span><input type="number" min="1" max="100" placeholder="Default" value={agent.maxSteps} onChange={(event) => setAgent({ ...agent, maxSteps: event.target.value })} /></label><label><span>Turn limit</span><input type="number" min="1" max="600" placeholder="Seconds" value={agent.wallTimeout} onChange={(event) => setAgent({ ...agent, wallTimeout: event.target.value })} /></label></div><footer><button type="button" className="secondary" onClick={() => setAgentOpen(false)}>Cancel</button><button type="submit"><Plus size={14} /> Create agent</button></footer></form></div> : null}
  </div>;
}
