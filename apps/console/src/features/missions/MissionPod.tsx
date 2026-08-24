import { FormEvent, useEffect, useState } from "react";
import {
  bootstrapMission, createMissionAgent, createMissionRun, createMissionTrigger,
  getMission, listProjectFiles, updateMission, verifyMissionDeliverable, retryMissionRun, cancelMissionRun, decideMissionCheckpoint, type MissionAgentInput,
  type MissionDeliverable, type MissionOverview,
} from "../../api";

const tabs = ["Overview", "Crew", "Work", "Feed", "Sources", "Deliverables", "Runs", "Automation"] as const;
type Tab = typeof tabs[number];
const text = (value: unknown) => typeof value === "string" ? value : "";
const commaList = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);

export function MissionPod({ projectId, projectTitle, projectPrompt = "", artifactKind = "report", onClose }: { projectId: string; projectTitle: string; projectPrompt?: string; artifactKind?: string; onClose: () => void }) {
  const [data, setData] = useState<MissionOverview | null>(null);
  const [files, setFiles] = useState<{ name: string; size: number; type: string }[]>([]);
  const [tab, setTab] = useState<Tab>("Overview");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [agent, setAgent] = useState({ name: "", role: "", mandate: "", responsibilities: "", dataScope: "", tools: "", autonomy: "assist" as MissionAgentInput["autonomy"], escalation: "", maxSteps: "", wallTimeout: "" });
  const [agentFormOpen, setAgentFormOpen] = useState(false);
  const [trigger, setTrigger] = useState({ type: "cron", cron: "0 9 * * 1", fact: "", operator: "eq", value: "", timezone: "UTC", policy: "queue" });
  const [editingMission, setEditingMission] = useState(false);
  const [missionDraft, setMissionDraft] = useState({ objective: "", definitionOfDone: "" });

  const refresh = async () => {
    setLoading(true);
    try { setData(await getMission(projectId)); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load Mission"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, [projectId]);
  useEffect(() => {
    if (!data?.mission) return;
    setMissionDraft({
      objective: text(data.mission.objective),
      definitionOfDone: text(data.mission.definition_of_done),
    });
  }, [data?.mission, projectId]);
  useEffect(() => {
    if (!(["Runs", "Feed", "Work"] as Tab[]).includes(tab)) return;
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => window.clearInterval(timer);
  }, [projectId, tab]);
  useEffect(() => { if (tab === "Sources" || tab === "Overview") void listProjectFiles(projectId).then(setFiles).catch(() => setFiles([])); }, [projectId, tab]);

  const bootstrap = async () => {
    const deliverable = artifactKind === "data_app" ? "working application" : artifactKind === "slides" ? "slide deck" : artifactKind === "one_pager" ? "one-page brief" : "report";
    try {
      await bootstrapMission(projectId, {
        title: projectTitle,
        objective: projectPrompt.trim() || projectTitle,
        definition_of_done: `Produce a source-grounded ${deliverable}, resolve or clearly flag material exceptions, and obtain human verification of the exact final version.`,
      });
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create Mission"); }
  };
  const queueRun = async () => { try { await createMissionRun(projectId); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not queue run"); } };
  const saveMission = async (event: FormEvent) => {
    event.preventDefault();
    if (!data?.mission) return;
    try {
      await updateMission(projectId, {
        expected_revision: Number(data.mission.revision),
        objective: missionDraft.objective,
        definition_of_done: missionDraft.definitionOfDone,
      });
      setEditingMission(false);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not update Mission"); }
  };
  const submitAgent = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const budget = {
        ...(agent.maxSteps ? { max_steps: Number(agent.maxSteps) } : {}),
        ...(agent.wallTimeout ? { wall_timeout_seconds: Number(agent.wallTimeout) } : {}),
      };
      await createMissionAgent(projectId, { name: agent.name, role: agent.role, mandate: agent.mandate, responsibilities: commaList(agent.responsibilities), data_scope: commaList(agent.dataScope), tools: commaList(agent.tools), autonomy: agent.autonomy, escalation_actor_id: agent.escalation || null, budget });
      setAgent({ name: "", role: "", mandate: "", responsibilities: "", dataScope: "", tools: "", autonomy: "assist", escalation: "", maxSteps: "", wallTimeout: "" });
      setAgentFormOpen(false);
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
  const verify = async (deliverable: MissionDeliverable) => { try { await verifyMissionDeliverable(projectId, deliverable.id, deliverable.content_hash, deliverable.revision); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Verification is not permitted"); } };
  const mission = data?.mission;
  const graphStatus = data?.readiness?.graph?.status || "missing";
  const objectiveReady = Boolean(text(mission?.objective).trim());
  const doneReady = Boolean(text(mission?.definition_of_done).trim());
  const crewReady = Boolean(data?.agents.length);
  const graphReady = graphStatus === "approved";
  const canQueue = Boolean(mission && objectiveReady && doneReady && crewReady && graphReady);
  const openAgentForm = () => { setTab("Crew"); setAgentFormOpen(true); };
  return <section className="mission-pod" aria-label="Mission Pod">
    <header className="mission-header"><div><span className="mission-kicker">MISSION</span><h2>{projectTitle}</h2></div><div className="mission-header-actions">{mission ? <button className="mission-agent-action" onClick={openAgentForm}>＋ New agent</button> : null}<button onClick={() => void queueRun()} disabled={!canQueue} title={!canQueue ? "Complete the readiness checks before running" : "Queue a governed agent run"}>{canQueue ? "Run Mission" : "Not ready"}</button><button className="mission-quiet" onClick={onClose}>Back to chat</button></div></header>
    {error ? <p className="mission-error" role="alert">{error}</p> : null}
    {loading ? <p className="mission-empty">Loading Mission…</p> : !mission ? <div className="mission-empty"><p>This legacy project has no Mission definition yet.</p><button onClick={() => void bootstrap()}>Make this a Mission</button></div> : <>
      <nav className="mission-tabs">{tabs.map((name) => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{name}</button>)}</nav>
      {tab === "Overview" && <div className="mission-overview">
        <section className="mission-readiness" aria-label="Mission readiness">
          <div className="mission-section-head"><div><span className="mission-kicker">READINESS</span><h3>{canQueue ? "Ready for a governed run" : "Finish setup before the first run"}</h3></div></div>
          <div className="mission-checks">
            <span className={objectiveReady && doneReady ? "ready" : ""}>{objectiveReady && doneReady ? "✓" : "1"} Outcome defined</span>
            <span className={files.length ? "ready" : "optional"}>{files.length ? "✓" : "2"} {files.length ? `${files.length} source${files.length === 1 ? "" : "s"} attached` : "Add sources if this Mission needs data"}</span>
            <button type="button" className={crewReady ? "ready" : ""} onClick={openAgentForm}>{crewReady ? "✓" : "3"} {crewReady ? `${data.agents.length} agent${data.agents.length === 1 ? "" : "s"} configured · Manage crew` : "Add your first agent"}</button>
            <span className={graphReady ? "ready" : ""}>{graphReady ? "✓" : "4"} {graphStatus === "approved" ? `Operation Graph revision ${data.readiness.graph.revision} approved` : graphStatus === "pending_approval" ? "Review and approve the Operation Graph" : graphStatus === "invalid" ? "Repair the Operation Graph" : "Generate the Operation Graph"}</span>
          </div>
        </section>
        {editingMission ? <form className="mission-form mission-definition-form" onSubmit={saveMission}><h3>Define the Mission</h3><label>Objective<textarea required value={missionDraft.objective} onChange={(event) => setMissionDraft({ ...missionDraft, objective: event.target.value })}/></label><label>Definition of done<textarea required value={missionDraft.definitionOfDone} onChange={(event) => setMissionDraft({ ...missionDraft, definitionOfDone: event.target.value })}/></label><div><button type="submit">Save Mission</button><button type="button" className="mission-quiet" onClick={() => setEditingMission(false)}>Cancel</button></div></form> : <div className="mission-grid"><article><label>Objective</label><p>{text(mission.objective)}</p></article><article><label>Definition of done</label><p>{text(mission.definition_of_done)}</p></article><article><label>Status</label><p>{text(mission.status)}</p></article><article><label>Execution</label><p>Managed agent work with human checkpoints and exact deliverable verification.</p></article><article><label>Mission budget</label><p>Optional caps: 1–100 tool actions and 1–600 seconds per agent turn.</p></article><article className="mission-edit-card"><label>Mission contract</label><p>Change the outcome or verification criteria before the next run.</p><button onClick={() => setEditingMission(true)}>Edit definition</button></article></div>}
      </div>}
      {tab === "Crew" && <div className="mission-crew">
        <div className="mission-crew-head"><div><span className="mission-kicker">HUMANS + AGENTS</span><h3>Mission crew</h3><p>Define a durable role around the outcome. Missions manages execution, permissions, and checkpoints behind the scenes.</p></div>{!agentFormOpen ? <button onClick={() => setAgentFormOpen(true)}>＋ New agent</button> : null}</div>
        {agentFormOpen ? <form className="mission-form mission-agent-form" onSubmit={submitAgent}>
          <div className="mission-form-head"><div><span className="mission-kicker">CREATE AGENT</span><h3>Add a specialist to this Mission</h3><p>Describe the job this agent owns. Runtime and model routing are managed by Missions.</p></div><button type="button" className="mission-form-close" aria-label="Close agent form" onClick={() => setAgentFormOpen(false)}>×</button></div>
          <div className="mission-form-grid">
            <label><span>Name</span><input required placeholder="e.g. Reconciliation analyst" value={agent.name} onChange={(e) => setAgent({ ...agent, name: e.target.value })}/></label>
            <label><span>Role</span><input required placeholder="e.g. Finance operations" value={agent.role} onChange={(e) => setAgent({ ...agent, role: e.target.value })}/></label>
            <label className="mission-form-wide"><span>Mandate</span><small>What outcome is this agent accountable for?</small><textarea required placeholder="Reconcile incoming invoices against approved purchase orders, surface exceptions, and prepare evidence for review." value={agent.mandate} onChange={(e) => setAgent({ ...agent, mandate: e.target.value })}/></label>
            <label className="mission-form-wide"><span>Responsibilities</span><small>Comma-separated durable responsibilities.</small><input placeholder="match records, investigate exceptions, prepare review pack" value={agent.responsibilities} onChange={(e) => setAgent({ ...agent, responsibilities: e.target.value })}/></label>
            <label><span>Data access</span><small>Files or folders this role may read.</small><input placeholder="invoices/, purchase-orders/" value={agent.dataScope} onChange={(e) => setAgent({ ...agent, dataScope: e.target.value })}/></label>
            <label><span>Capabilities</span><small>Actions this role needs.</small><input placeholder="document.read, artifact.write" value={agent.tools} onChange={(e) => setAgent({ ...agent, tools: e.target.value })}/></label>
            <label><span>Autonomy</span><select value={agent.autonomy} onChange={(e) => setAgent({ ...agent, autonomy: e.target.value as MissionAgentInput["autonomy"] })}><option value="assist">Assist — draft for a human</option><option value="execute_safely">Execute safely — act within scope</option><option value="operate_with_checkpoints">Checkpoints — ask before consequential work</option></select></label>
            <div className="mission-budget-fields"><label><span>Max tool actions</span><input type="number" min="1" max="100" placeholder="Default" value={agent.maxSteps} onChange={(e) => setAgent({ ...agent, maxSteps: e.target.value })}/></label><label><span>Max turn time</span><input type="number" min="1" max="600" placeholder="Seconds" value={agent.wallTimeout} onChange={(e) => setAgent({ ...agent, wallTimeout: e.target.value })}/></label></div>
          </div>
          <div className="mission-form-actions"><button type="button" className="mission-quiet" onClick={() => setAgentFormOpen(false)}>Cancel</button><button type="submit">Create agent</button></div>
        </form> : null}
        <div className="mission-agent-list">{data.agents.length ? data.agents.map((item) => <article key={text(item.id)}><div className="mission-agent-avatar">{text(item.name).slice(0, 1).toUpperCase()}</div><div><strong>{text(item.name)}</strong><span>{text(item.role)} · {text(item.autonomy).replaceAll("_", " ")}</span><p>{text(item.mandate)}</p></div></article>) : <div className="mission-empty mission-crew-empty"><p>No agents yet. Add a specialist role to make this Mission runnable.</p><button onClick={() => setAgentFormOpen(true)}>Create the first agent</button></div>}</div>
      </div>}
      {tab === "Runs" && <div className="mission-list">{data.runs.length === 0 ? <p className="mission-empty">No runs yet. Complete Mission readiness, then start the first governed run.</p> : data.runs.map((item) => <article key={text(item.id)}><strong>{text(item.status).replaceAll("_", " ")}</strong><span>{text((item.execution_profile as Record<string, unknown>)?.profile)} · {text(item.created_at)} · {text(item.completed_at) || "in progress"}</span><p>Current agent: {text(item.current_agent_id) || "waiting"}{text((item.error as Record<string, unknown>)?.message) ? ` · ${text((item.error as Record<string, unknown>)?.message)}` : ""}</p>{["failed", "awaiting_approval"].includes(text(item.status)) && <button onClick={() => void retryMissionRun(projectId, text(item.id), Number(item.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Retry failed"))}>Retry</button>}{["queued", "awaiting_approval"].includes(text(item.status)) && <button onClick={() => void cancelMissionRun(projectId, text(item.id), Number(item.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Cancel failed"))}>Cancel</button>}</article>)}</div>}
      {tab === "Deliverables" && <div className="mission-list">{data.deliverables.map((raw) => { const item = raw as unknown as MissionDeliverable; const stagedCode = Boolean((raw.validation_evidence as Record<string, unknown>[] | undefined)?.some((entry) => Boolean(entry?.intended_target))); return <article key={item.id}><strong>{item.name}</strong><span>v{item.version} · {item.state} · {item.content_hash}</span>{stagedCode && item.state !== "verified" ? <p>Staged code candidate: verifying this exact file promotes only this file into the live app.</p> : null}{item.state !== "verified" && <button onClick={() => void verify(item)}>Verify exact version</button>}</article>; })}</div>}
      {tab === "Automation" && <div className="mission-list"><form className="mission-form" onSubmit={submitTrigger}><h3>Add automation</h3><p>Cron schedules run automatically after the Mission has an approved Operation Graph. Conditions run only when an integration submits a non-empty typed fact event.</p><select value={trigger.type} onChange={(e) => setTrigger({ ...trigger, type: e.target.value })}><option value="cron">Cron</option><option value="condition">Condition</option></select>{trigger.type === "cron" ? <input required placeholder="Cron" value={trigger.cron} onChange={(e) => setTrigger({ ...trigger, cron: e.target.value })}/> : <><input required placeholder="Fact" value={trigger.fact} onChange={(e) => setTrigger({ ...trigger, fact: e.target.value })}/><select value={trigger.operator} onChange={(e) => setTrigger({ ...trigger, operator: e.target.value })}><option value="eq">equals</option><option value="gte">at least</option><option value="contains">contains</option></select><input required placeholder="Value" value={trigger.value} onChange={(e) => setTrigger({ ...trigger, value: e.target.value })}/></>}<input value={trigger.timezone} onChange={(e) => setTrigger({ ...trigger, timezone: e.target.value })}/><select value={trigger.policy} onChange={(e) => setTrigger({ ...trigger, policy: e.target.value })}><option value="queue">Queue</option><option value="skip">Skip</option><option value="replace">Replace</option><option value="merge">Merge</option></select><button>Add automation</button></form>{data.triggers.map((item) => <article key={text(item.id)}><strong>{text(item.type)}</strong><span>{text(item.next_due_at)} · {text(item.concurrency_policy)}</span></article>)}</div>}
      {tab === "Sources" && <div className="mission-list">{files.map((file) => <article key={file.name}><strong>{file.name}</strong><span>{file.type} · {file.size} bytes</span></article>)}</div>}
      {tab === "Work" && <div className="mission-list">{data.approvals.filter((item) => text(item.status) === "pending").map((item) => { const actionable = ["checkpoint_required", "recovery_retry"].includes(text(item.code)); const run = data.runs.find((row) => text(row.id) === text(item.run_id)); return <article key={text(item.id)}><strong>{text(item.code)}</strong><p>{text(item.message)}</p>{actionable ? <><button onClick={() => void decideMissionCheckpoint(projectId, text(item.id), "approve", Number(item.revision), Number(run?.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Checkpoint decision failed"))}>Approve</button><button onClick={() => void decideMissionCheckpoint(projectId, text(item.id), "reject", Number(item.revision), Number(run?.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Checkpoint decision failed"))}>Reject</button></> : <p>Fix the requirement, then retry this run.</p>}</article>; })}{data.runs.filter((run) => text(run.status) === "awaiting_approval" && !["checkpoint_required", "recovery_retry"].includes(text((run.error as Record<string, unknown>)?.code))).map((run) => <article key={`block-${text(run.id)}`}><strong>{text((run.error as Record<string, unknown>)?.code) || "requirement blocked"}</strong><p>{text((run.error as Record<string, unknown>)?.message) || "Fix the requirement, then retry this run."}</p><p>Fix the requirement, then retry this run.</p></article>)}</div>}
      {tab === "Feed" && <div className="mission-list">{data.events.map((item) => <article key={text(item.id)}><strong>{text(item.type)}</strong><span>{text(item.timestamp)}</span><p>{text((item.payload as Record<string, unknown>)?.message) || text((item.payload as Record<string, unknown>)?.response)}</p></article>)}</div>}
    </>}
  </section>;
}
