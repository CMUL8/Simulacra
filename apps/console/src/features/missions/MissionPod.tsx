import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot, CalendarClock, Check, ChevronRight, Circle, FileCheck2, FileText,
  Flag, Play, Plus, Settings2, ShieldCheck, Users, X,
} from "lucide-react";
import {
  bootstrapMission, createMissionAgent, createMissionRun, createMissionTrigger,
  decideMissionCheckpoint, getMission, updateMission, verifyMissionDeliverable,
  type MissionAgentInput, type MissionDeliverable, type MissionOverview,
} from "../../api";

const text = (value: unknown) => typeof value === "string" ? value : "";
const pretty = (value: unknown) => text(value).replaceAll("_", " ");
const friendlyError = (value: string) => {
  if (/permission denied|errno 13/i.test(value)) return "Mission storage is temporarily unavailable. Your work is safe; retry in a moment.";
  if (/credential_unavailable/i.test(value)) return "Execution has not been activated for this deployment yet.";
  return value.replace(/^Error:\s*/i, "");
};

type AgentScope = "sources" | "documents" | "code";
type AgentDraft = { name: string; mandate: string; scope: AgentScope; autonomy: MissionAgentInput["autonomy"]; };

const emptyAgent: AgentDraft = {
  name: "", mandate: "", scope: "documents", autonomy: "operate_with_checkpoints",
};

const scopeConfig: Record<AgentScope, Pick<MissionAgentInput, "role" | "responsibilities" | "data_scope" | "tools">> = {
  sources: { role: "Research specialist", responsibilities: ["Read Mission sources", "Return grounded findings with evidence"], data_scope: ["sources/"], tools: ["document.read", "artifact.write"] },
  documents: { role: "Mission specialist", responsibilities: ["Work from Mission sources", "Create reviewable deliverables"], data_scope: ["sources/", "outputs/"], tools: ["document.read", "artifact.write"] },
  code: { role: "Product builder", responsibilities: ["Work from approved requirements and sources", "Build and test staged changes for human verification"], data_scope: ["sources/", "app/"], tools: ["document.read", "code.write"] },
};

const autonomyLabel = (value: unknown) => value === "assist" ? "returns drafts for review" : value === "execute_safely" ? "works within approved scope" : "asks before consequential work";

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
  const activeRun = data?.runs.find((item) => !["succeeded", "failed", "cancelled", "expired"].includes(text(item.status)));
  const pendingOutput = data?.deliverables.filter((item) => text(item.state) !== "verified") ?? [];
  const pendingApprovals = data?.approvals.filter((item) => item.status === "pending") ?? [];

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
        role: scopeConfig[agent.scope].role,
        mandate: agent.mandate,
        responsibilities: scopeConfig[agent.scope].responsibilities,
        data_scope: scopeConfig[agent.scope].data_scope,
        tools: scopeConfig[agent.scope].tools,
        autonomy: agent.autonomy,
        escalation_actor_id: null,
        budget: {},
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

  const decide = async (approvalId: string, decision: "approve" | "reject") => {
    const approval = pendingApprovals.find((item) => item.id === approvalId);
    const run = data?.runs.find((item) => item.id === approval?.run_id);
    if (!approval || !run) return;
    try {
      await decideMissionCheckpoint(projectId, approval.id, decision, approval.revision, run.revision);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not record the decision"); }
  };

  return <div className="mission-panel-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <aside className="mission-panel" aria-label="Mission details">
      <header className="mission-panel-header"><div><span><Flag size={13} /> MISSION</span><h2>{projectTitle}</h2></div><button type="button" aria-label="Close Mission details" onClick={onClose}><X size={17} /></button></header>
      {error ? <div className="mission-panel-error" role="alert">{friendlyError(error)}<details><summary>Technical details</summary><code>{error}</code></details></div> : null}
      {loading && !data ? <div className="mission-panel-loading">Opening Mission…</div> : !mission ? <div className="mission-panel-bootstrap"><Flag size={22} /><h3>Create the Mission workspace</h3><p>Add a durable outcome, crew, and verification boundary to this project.</p><button onClick={() => void bootstrap()}>Create Mission</button></div> : <div className="mission-panel-scroll">
        <section className="mission-panel-status"><div><span className={`mission-live-dot${activeRun ? " active" : ""}`} /><div><strong>{activeRun ? pretty(activeRun.status) : pretty(mission.status) || "draft"}</strong><small>{activeRun ? "The Mission crew is working" : canRun ? "Ready to start" : "Finish setup to start"}</small></div></div><button disabled={!canRun || Boolean(activeRun)} onClick={() => void run()}><Play size={13} fill="currentColor" /> Start</button></section>

        {!canRun ? <section className="mission-panel-setup"><header><span>READY TO START</span><strong>{[objectiveReady, crewReady, graphReady].filter(Boolean).length}/3</strong></header><button className={objectiveReady ? "done" : ""} onClick={() => setEditing(true)}>{objectiveReady ? <Check size={14} /> : <Circle size={14} />}<span><strong>Outcome</strong><small>{objectiveReady ? "Defined" : "Describe what done means"}</small></span><ChevronRight size={14} /></button><button className={crewReady ? "done" : ""} onClick={() => setAgentOpen(true)}>{crewReady ? <Check size={14} /> : <Circle size={14} />}<span><strong>Crew</strong><small>{crewReady ? `${data.agents.length} agent${data.agents.length === 1 ? "" : "s"}` : "Add an agent"}</small></span><ChevronRight size={14} /></button><button className={graphReady ? "done" : ""} onClick={onOpenTasks}>{graphReady ? <Check size={14} /> : <Circle size={14} />}<span><strong>Access</strong><small>{graphReady ? "Approved" : "Confirm how the crew will work"}</small></span><ChevronRight size={14} /></button></section> : null}

        <section className="mission-panel-section" ref={crewRef}><header><div><span><Users size={14} /> CREW</span><strong>{data.agents.length} agent{data.agents.length === 1 ? "" : "s"}</strong></div><button onClick={() => setAgentOpen(true)}><Plus size={14} /> Agent</button></header>{data.agents.length ? <ul className="mission-panel-crew">{data.agents.map((item) => <li key={text(item.id)}><i>{text(item.name).slice(0, 1).toUpperCase()}</i><span><strong>{text(item.name)}</strong><small>{text(item.role)} · {autonomyLabel(item.autonomy)}</small></span><em /></li>)}</ul> : <button className="mission-panel-empty-action" onClick={() => setAgentOpen(true)}><Bot size={18} /><span><strong>Add the first agent</strong><small>Give an agent a job inside this Mission.</small></span></button>}</section>

        {pendingApprovals.length ? <section className="mission-panel-section"><header><div><span><ShieldCheck size={14} /> YOUR DECISION</span><strong>{pendingApprovals.length}</strong></div></header><ul className="mission-panel-approvals">{pendingApprovals.map((approval) => { const run = data.runs.find((item) => item.id === approval.run_id); const agent = data.agents.find((item) => item.id === approval.agent_id); return <li key={approval.id}><div><strong>{agent?.name || "Mission agent"} is waiting</strong><small>{run?.trigger_snapshot?.note || "Approve this checkpoint to continue the run."}</small></div><span><button className="secondary" onClick={() => void decide(approval.id, "reject")}>Reject</button><button onClick={() => void decide(approval.id, "approve")}>Approve</button></span></li>; })}</ul></section> : null}

        {pendingOutput.length ? <section className="mission-panel-section"><header><div><span><ShieldCheck size={14} /> NEEDS REVIEW</span><strong>{pendingOutput.length}</strong></div></header><ul className="mission-panel-output">{pendingOutput.map((raw) => { const item = raw as unknown as MissionDeliverable; return <li key={item.id}><FileCheck2 size={16} /><span><strong>{item.name}</strong><small>Version {item.version} · exact output</small></span><button onClick={() => void verify(item)}>Verify</button></li>; })}</ul></section> : null}

        <section className="mission-panel-links"><button onClick={onOpenTasks}><FileCheck2 size={15} /><span><strong>Work</strong><small>Assignments, progress, and decisions</small></span><ChevronRight size={14} /></button><button onClick={onOpenFiles}><FileText size={15} /><span><strong>Files</strong><small>Sources, outputs, and evidence</small></span><ChevronRight size={14} /></button></section>

        <details className="mission-panel-details" open={editing}><summary><Settings2 size={14} /> Outcome</summary>{editing ? <form onSubmit={saveMission}><label>What should this Mission accomplish?<textarea required value={draft.objective} onChange={(event) => setDraft({ ...draft, objective: event.target.value })} /></label><label>What does done look like?<textarea required value={draft.definitionOfDone} onChange={(event) => setDraft({ ...draft, definitionOfDone: event.target.value })} /></label><div><button type="button" className="secondary" onClick={() => setEditing(false)}>Cancel</button><button type="submit">Save</button></div></form> : <div className="mission-panel-definition"><p>{text(mission.objective)}</p><span>DONE WHEN</span><p>{text(mission.definition_of_done)}</p><button onClick={() => setEditing(true)}>Edit</button></div>}</details>

        <details className="mission-panel-details"><summary><CalendarClock size={14} /> Automation <em>{data.triggers.length || ""}</em></summary><form className="mission-panel-automation" onSubmit={submitTrigger}><label>Run this Mission<select value={trigger.type} onChange={(event) => setTrigger({ ...trigger, type: event.target.value })}><option value="cron">On a schedule</option><option value="condition">When a condition is reported</option></select></label>{trigger.type === "cron" ? <><label>Schedule<select aria-label="Schedule preset" value={["0 9 * * *", "0 9 * * 1-5", "0 9 * * 1"].includes(trigger.cron) ? trigger.cron : "custom"} onChange={(event) => { if (event.target.value !== "custom") setTrigger({ ...trigger, cron: event.target.value }); }}><option value="0 9 * * *">Every day at 9:00</option><option value="0 9 * * 1-5">Weekdays at 9:00</option><option value="0 9 * * 1">Every Monday at 9:00</option><option value="custom">Custom schedule</option></select></label>{!["0 9 * * *", "0 9 * * 1-5", "0 9 * * 1"].includes(trigger.cron) ? <label>Custom cron<input required aria-label="Cron schedule" value={trigger.cron} onChange={(event) => setTrigger({ ...trigger, cron: event.target.value })} /></label> : null}</> : <><label>Fact<input required placeholder="e.g. invoices_ready" value={trigger.fact} onChange={(event) => setTrigger({ ...trigger, fact: event.target.value })} /></label><label>Condition<select value={trigger.operator} onChange={(event) => setTrigger({ ...trigger, operator: event.target.value })}><option value="eq">equals</option><option value="gte">is at least</option><option value="contains">contains</option></select></label><label>Value<input required placeholder="Expected value" value={trigger.value} onChange={(event) => setTrigger({ ...trigger, value: event.target.value })} /></label></>}<label>Timezone<input aria-label="Timezone" value={trigger.timezone} onChange={(event) => setTrigger({ ...trigger, timezone: event.target.value })} /></label><details className="mission-automation-advanced"><summary>Advanced behavior</summary><label>When work is already running<select aria-label="Concurrency policy" value={trigger.policy} onChange={(event) => setTrigger({ ...trigger, policy: event.target.value })}><option value="queue">Queue the next run</option><option value="skip">Skip this occurrence</option><option value="replace">Replace queued work</option><option value="merge">Merge into a follow-up</option></select></label></details><button><Plus size={13} /> Add automation</button></form>{data.triggers.length ? <ul className="mission-trigger-list">{data.triggers.map((item, index) => <li key={text(item.id) || index}><span>{item.type === "cron" ? "Scheduled Mission" : "Condition-based Mission"}</span><small>{item.type === "cron" ? `${text(item.cron)} · ${text(item.timezone) || "UTC"}` : `When ${text((item.condition as Record<string, unknown> | undefined)?.fact) || "condition matches"}`}</small></li>)}</ul> : null}</details>
      </div>}
    </aside>

    {agentOpen ? <div className="mission-agent-modal-scrim"><form className="mission-agent-modal" onSubmit={submitAgent}><header><div><span>ADD AGENT</span><h2>Add an agent to the crew</h2><p>Give the agent a name, a job, and access. Missions handles everything else.</p></div><button type="button" aria-label="Close agent form" onClick={() => setAgentOpen(false)}><X size={18} /></button></header><div className="mission-agent-fields"><div className="mission-agent-presets wide"><span>START WITH A JOB</span><button type="button" onClick={() => setAgent({ ...emptyAgent, name: "Operations analyst", mandate: "Reconcile records, surface exceptions, and prepare exact evidence for human review.", scope: "documents" })}>Operations</button><button type="button" onClick={() => setAgent({ ...emptyAgent, name: "Research analyst", mandate: "Ground findings in Mission sources and produce a reviewable, cited brief.", scope: "sources" })}>Research</button><button type="button" onClick={() => setAgent({ ...emptyAgent, name: "Product builder", mandate: "Turn approved requirements and source data into a working application for human verification.", scope: "code" })}>Builder</button></div><label className="wide"><span>Name</span><input autoFocus required placeholder="e.g. Operations analyst" value={agent.name} onChange={(event) => setAgent({ ...agent, name: event.target.value })} /></label><label className="wide"><span>What should this agent own?</span><textarea required placeholder="Describe the job and what the agent should return to the Mission." value={agent.mandate} onChange={(event) => setAgent({ ...agent, mandate: event.target.value })} /></label><label><span>What can it work with?</span><select value={agent.scope} onChange={(event) => setAgent({ ...agent, scope: event.target.value as AgentScope })}><option value="sources">Mission sources</option><option value="documents">Sources and deliverables</option><option value="code">Sources and the Mission app</option></select></label><label><span>When should it ask you?</span><select value={agent.autonomy} onChange={(event) => setAgent({ ...agent, autonomy: event.target.value as MissionAgentInput["autonomy"] })}><option value="operate_with_checkpoints">Before consequential work</option><option value="assist">Before anything is final</option><option value="execute_safely">Only when outside its scope</option></select></label><p className="mission-agent-managed wide">Runtime, models, memory, and execution limits are managed by Missions.</p></div><footer><button type="button" className="secondary" onClick={() => setAgentOpen(false)}>Cancel</button><button type="submit"><Plus size={14} /> Add agent</button></footer></form></div> : null}
  </div>;
}
