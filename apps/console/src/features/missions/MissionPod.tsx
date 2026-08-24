import { FormEvent, useEffect, useState } from "react";
import {
  bootstrapMission, createMissionAgent, createMissionRun, createMissionTrigger,
  getMission, listProjectFiles, verifyMissionDeliverable, retryMissionRun, cancelMissionRun, decideMissionCheckpoint, type MissionAgentInput,
  type MissionDeliverable, type MissionOverview,
} from "../../api";

const tabs = ["Overview", "Feed", "Work", "Sources", "Deliverables", "Runs", "Automation", "Crew"] as const;
type Tab = typeof tabs[number];
const text = (value: unknown) => typeof value === "string" ? value : "";
const commaList = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);

export function MissionPod({ projectId, projectTitle, onClose }: { projectId: string; projectTitle: string; onClose: () => void }) {
  const [data, setData] = useState<MissionOverview | null>(null);
  const [files, setFiles] = useState<{ name: string; size: number; type: string }[]>([]);
  const [tab, setTab] = useState<Tab>("Overview");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [agent, setAgent] = useState({ name: "", role: "", mandate: "", responsibilities: "", dataScope: "", tools: "", autonomy: "assist" as MissionAgentInput["autonomy"], escalation: "", maxSteps: "", wallTimeout: "" });
  const [trigger, setTrigger] = useState({ type: "cron", cron: "0 9 * * 1", fact: "", operator: "eq", value: "", timezone: "UTC", policy: "queue" });

  const refresh = async () => {
    setLoading(true);
    try { setData(await getMission(projectId)); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load Mission"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, [projectId]);
  useEffect(() => {
    if (!(["Runs", "Feed", "Work"] as Tab[]).includes(tab)) return;
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => window.clearInterval(timer);
  }, [projectId, tab]);
  useEffect(() => { if (tab === "Sources") void listProjectFiles(projectId).then(setFiles).catch(() => setFiles([])); }, [projectId, tab]);

  const bootstrap = async () => { try { await bootstrapMission(projectId, { title: projectTitle, objective: "", definition_of_done: "" }); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create Mission"); } };
  const queueRun = async () => { try { await createMissionRun(projectId); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not queue run"); } };
  const submitAgent = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const budget = {
        ...(agent.maxSteps ? { max_steps: Number(agent.maxSteps) } : {}),
        ...(agent.wallTimeout ? { wall_timeout_seconds: Number(agent.wallTimeout) } : {}),
      };
      await createMissionAgent(projectId, { name: agent.name, role: agent.role, mandate: agent.mandate, responsibilities: commaList(agent.responsibilities), data_scope: commaList(agent.dataScope), tools: commaList(agent.tools), autonomy: agent.autonomy, escalation_actor_id: agent.escalation || null, budget });
      setAgent({ name: "", role: "", mandate: "", responsibilities: "", dataScope: "", tools: "", autonomy: "assist", escalation: "", maxSteps: "", wallTimeout: "" });
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
  return <section className="mission-pod" aria-label="Mission Pod">
    <header className="mission-header"><div><span className="mission-kicker">MISSION POD</span><h2>{projectTitle}</h2></div><div><button onClick={() => void queueRun()} disabled={!mission}>Queue run</button><button className="mission-quiet" onClick={onClose}>Back to chat</button></div></header>
    {error ? <p className="mission-error" role="alert">{error}</p> : null}
    {loading ? <p className="mission-empty">Loading Mission…</p> : !mission ? <div className="mission-empty"><p>This legacy project has no Mission definition yet.</p><button onClick={() => void bootstrap()}>Make this a Mission</button></div> : <>
      <nav className="mission-tabs">{tabs.map((name) => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{name}</button>)}</nav>
      {tab === "Overview" && <div className="mission-grid"><article><label>Objective</label><p>{text(mission.objective) || "No objective defined."}</p></article><article><label>Definition of done</label><p>{text(mission.definition_of_done) || "Not defined."}</p></article><article><label>Status</label><p>{text(mission.status)}</p></article><article><label>Runtime</label><p>Codex-only execution; run state is refreshed every two seconds while viewing live work.</p></article><article><label>Mission budget</label><p>Optional caps: max steps 1–100; wall time 1–600 seconds. Each agent receives the most restrictive applicable limit.</p></article></div>}
      {tab === "Crew" && <div className="mission-list"><form className="mission-form" onSubmit={submitAgent}><h3>Add Codex agent</h3><input required placeholder="Name" value={agent.name} onChange={(e) => setAgent({ ...agent, name: e.target.value })}/><input required placeholder="Role" value={agent.role} onChange={(e) => setAgent({ ...agent, role: e.target.value })}/><textarea required placeholder="Mandate" value={agent.mandate} onChange={(e) => setAgent({ ...agent, mandate: e.target.value })}/><input placeholder="Responsibilities (comma-separated)" value={agent.responsibilities} onChange={(e) => setAgent({ ...agent, responsibilities: e.target.value })}/><input placeholder="Data scope, e.g. docs/readme.md" value={agent.dataScope} onChange={(e) => setAgent({ ...agent, dataScope: e.target.value })}/><input placeholder="Tools: document.read, code.read, artifact.write, code.write" value={agent.tools} onChange={(e) => setAgent({ ...agent, tools: e.target.value })}/><select value={agent.autonomy} onChange={(e) => setAgent({ ...agent, autonomy: e.target.value as MissionAgentInput["autonomy"] })}><option value="assist">Assist</option><option value="execute_safely">Execute safely</option><option value="operate_with_checkpoints">Operate with checkpoints</option></select><input placeholder="Escalation actor ID" value={agent.escalation} onChange={(e) => setAgent({ ...agent, escalation: e.target.value })}/><input type="number" min="1" max="100" placeholder="Max tool actions (1–100)" value={agent.maxSteps} onChange={(e) => setAgent({ ...agent, maxSteps: e.target.value })}/><input type="number" min="1" max="600" placeholder="Wall time seconds (1–600)" value={agent.wallTimeout} onChange={(e) => setAgent({ ...agent, wallTimeout: e.target.value })}/><p>Optional limits only. Mission and agent caps combine using the lower value; Codex is stopped before an over-budget tool action can complete.</p><button>Add agent</button></form>{data.agents.map((item) => <article key={text(item.id)}><strong>{text(item.name)}</strong><span>{text(item.role)} · {text(item.autonomy)}</span><p>{text(item.mandate)}</p></article>)}</div>}
      {tab === "Runs" && <div className="mission-list">{data.runs.map((item) => <article key={text(item.id)}><strong>{text(item.status)}</strong><span>{text((item.execution_profile as Record<string, unknown>)?.profile)} · {text(item.created_at)} · {text(item.completed_at) || "not completed"}</span><p>Current agent: {text(item.current_agent_id) || "waiting"} · {text((item.progress as Record<string, unknown>)?.completed)} complete · {text((item.error as Record<string, unknown>)?.message)}</p>{["failed", "awaiting_approval"].includes(text(item.status)) && <button onClick={() => void retryMissionRun(projectId, text(item.id), Number(item.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Retry failed"))}>Retry</button>}{["queued", "awaiting_approval"].includes(text(item.status)) && <button onClick={() => void cancelMissionRun(projectId, text(item.id), Number(item.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Cancel failed"))}>Cancel</button>}</article>)}</div>}
      {tab === "Deliverables" && <div className="mission-list">{data.deliverables.map((raw) => { const item = raw as unknown as MissionDeliverable; const stagedCode = Boolean((raw.validation_evidence as Record<string, unknown>[] | undefined)?.some((entry) => Boolean(entry?.intended_target))); return <article key={item.id}><strong>{item.name}</strong><span>v{item.version} · {item.state} · {item.content_hash}</span>{stagedCode && item.state !== "verified" ? <p>Staged code candidate: verifying this exact file promotes only this file into the live app.</p> : null}{item.state !== "verified" && <button onClick={() => void verify(item)}>Verify exact version</button>}</article>; })}</div>}
      {tab === "Automation" && <div className="mission-list"><form className="mission-form" onSubmit={submitTrigger}><h3>Add automation</h3><p>Cron schedules run automatically after the Mission has an approved Operation Graph. Conditions run only when an integration submits a non-empty typed fact event.</p><select value={trigger.type} onChange={(e) => setTrigger({ ...trigger, type: e.target.value })}><option value="cron">Cron</option><option value="condition">Condition</option></select>{trigger.type === "cron" ? <input required placeholder="Cron" value={trigger.cron} onChange={(e) => setTrigger({ ...trigger, cron: e.target.value })}/> : <><input required placeholder="Fact" value={trigger.fact} onChange={(e) => setTrigger({ ...trigger, fact: e.target.value })}/><select value={trigger.operator} onChange={(e) => setTrigger({ ...trigger, operator: e.target.value })}><option value="eq">equals</option><option value="gte">at least</option><option value="contains">contains</option></select><input required placeholder="Value" value={trigger.value} onChange={(e) => setTrigger({ ...trigger, value: e.target.value })}/></>}<input value={trigger.timezone} onChange={(e) => setTrigger({ ...trigger, timezone: e.target.value })}/><select value={trigger.policy} onChange={(e) => setTrigger({ ...trigger, policy: e.target.value })}><option value="queue">Queue</option><option value="skip">Skip</option><option value="replace">Replace</option><option value="merge">Merge</option></select><button>Add automation</button></form>{data.triggers.map((item) => <article key={text(item.id)}><strong>{text(item.type)}</strong><span>{text(item.next_due_at)} · {text(item.concurrency_policy)}</span></article>)}</div>}
      {tab === "Sources" && <div className="mission-list">{files.map((file) => <article key={file.name}><strong>{file.name}</strong><span>{file.type} · {file.size} bytes</span></article>)}</div>}
      {tab === "Work" && <div className="mission-list">{data.approvals.filter((item) => text(item.status) === "pending").map((item) => { const actionable = ["checkpoint_required", "recovery_retry"].includes(text(item.code)); const run = data.runs.find((row) => text(row.id) === text(item.run_id)); return <article key={text(item.id)}><strong>{text(item.code)}</strong><p>{text(item.message)}</p>{actionable ? <><button onClick={() => void decideMissionCheckpoint(projectId, text(item.id), "approve", Number(item.revision), Number(run?.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Checkpoint decision failed"))}>Approve</button><button onClick={() => void decideMissionCheckpoint(projectId, text(item.id), "reject", Number(item.revision), Number(run?.revision)).then(refresh).catch((e) => setError(e instanceof Error ? e.message : "Checkpoint decision failed"))}>Reject</button></> : <p>Fix the requirement, then retry this run.</p>}</article>; })}{data.runs.filter((run) => text(run.status) === "awaiting_approval" && !["checkpoint_required", "recovery_retry"].includes(text((run.error as Record<string, unknown>)?.code))).map((run) => <article key={`block-${text(run.id)}`}><strong>{text((run.error as Record<string, unknown>)?.code) || "requirement blocked"}</strong><p>{text((run.error as Record<string, unknown>)?.message) || "Fix the requirement, then retry this run."}</p><p>Fix the requirement, then retry this run.</p></article>)}</div>}
      {tab === "Feed" && <div className="mission-list">{data.events.map((item) => <article key={text(item.id)}><strong>{text(item.type)}</strong><span>{text(item.timestamp)}</span><p>{text((item.payload as Record<string, unknown>)?.message) || text((item.payload as Record<string, unknown>)?.response)}</p></article>)}</div>}
    </>}
  </section>;
}
