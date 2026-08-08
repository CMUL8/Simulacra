import { Building2, Shield } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_DESIGN_BRIEF,
  approveProject,
  cancelProjectJob,
  clearAuth,
  createProject,
  deployProject,
  fetchMe,
  getProject,
  getTenantId,
  getToken,
  listFixtureFiles,
  listProjectFiles,
  listProjects,
  patchDesignBrief,
  rollbackProject,
  sendChat,
  sendPlanChat,
  setTenantId,
  type AuthSession,
  type AuthUser,
  type DataRoomFile,
  type DesignBrief,
  type Project,
  type Snapshot,
  type Tenant,
} from "./api";
import { AdminPage } from "./components/AdminPage";
import { AgentShell } from "./components/AgentShell";
import { GovernancePage } from "./components/GovernancePage";
import { Landing } from "./components/Landing";
import { PreviewDrawer } from "./components/PreviewDrawer";
import { ProfileManageModal } from "./components/ProfileManageModal";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { useEventStream } from "./hooks/useEventStream";

type AppMode = "landing" | "plan" | "workspace" | "governance" | "admin";

function jobRunning(snap: Snapshot | null): boolean {
  const status = snap?.job?.status ?? snap?.project.job?.status;
  return status === "running" || status === "settling" || snap?.project.phase === "build";
}

export default function App({
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
}: {
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
}) {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [mode, setMode] = useState<AppMode>("landing");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [fixtureFiles, setFixtureFiles] = useState<DataRoomFile[]>([]);
  const [projectFiles, setProjectFiles] = useState<DataRoomFile[]>([]);
  const [goal, setGoal] = useState("");
  const [prompt, setPrompt] = useState("");
  const [designBrief, setDesignBrief] = useState<DesignBrief>(DEFAULT_DESIGN_BRIEF);
  const [dataAttached, setDataAttached] = useState(true);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiOk, setApiOk] = useState(true);
  const [govReturn, setGovReturn] = useState<AppMode>("landing");
  const [profileOpen, setProfileOpen] = useState(false);
  const pollRef = useRef<number | null>(null);

  const projectId = snapshot?.project.id ?? null;
  const { events: traces } = useEventStream(projectId);
  const running = busy || jobRunning(snapshot);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setAuthed(false);
      return;
    }
    fetchMe()
      .then((me) => {
        setUser(me.user);
        setTenants(me.tenants || []);
        if (me.tenant_id) setTenantId(me.tenant_id);
        setAuthed(true);
      })
      .catch(() => {
        clearAuth();
        setAuthed(false);
      });
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await listProjects());
      setApiOk(true);
    } catch {
      setApiOk(false);
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollUntilIdle = useCallback(
    (id: string) => {
      stopPolling();
      pollRef.current = window.setInterval(async () => {
        try {
          const snap = await getProject(id);
          setSnapshot(snap);
          const status = snap.job?.status ?? snap.project.job?.status ?? "idle";
          if (status === "idle" || status === "failed" || status === "cancelled" || snap.project.phase === "ready") {
            stopPolling();
            setBusy(false);
            if (snap.project.phase === "ready") {
              setMode("workspace");
              setPreviewOpen(true);
            }
            await refreshProjects();
          }
        } catch {
          /* keep polling briefly */
        }
      }, 1500);
    },
    [refreshProjects, stopPolling],
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  useEffect(() => {
    if (!authed) return;
    refreshProjects();
    listFixtureFiles().then(setFixtureFiles).catch(() => setFixtureFiles([]));
  }, [refreshProjects, authed]);

  useEffect(() => {
    const id = snapshot?.project.id;
    if (!id) {
      setProjectFiles(fixtureFiles);
      return;
    }
    listProjectFiles(id).then(setProjectFiles).catch(() => setProjectFiles(fixtureFiles));
  }, [snapshot?.project.id, fixtureFiles]);

  // When SSE says done, refresh snapshot
  useEffect(() => {
    const last = traces[traces.length - 1];
    if (!last || !projectId) return;
    if (last.type === "done" || (last.type === "error" && last.status === "fail")) {
      getProject(projectId)
        .then((snap) => {
          setSnapshot(snap);
          if (snap.project.phase === "ready") {
            setMode("workspace");
            setBusy(false);
            stopPolling();
          }
        })
        .catch(() => undefined);
    }
  }, [traces, projectId, stopPolling]);

  function handleSignOut() {
    const clerkOut = (window as unknown as { __simulacraClerkSignOut?: () => Promise<void> })
      .__simulacraClerkSignOut;
    clearAuth();
    setAuthed(false);
    setUser(null);
    setTenants([]);
    setProfileOpen(false);
    if (clerkOut) void clerkOut();
  }

  function handleAuthed(session: AuthSession) {
    setUser(session.user);
    setTenants(session.tenants || []);
    setAuthed(true);
    setProfileOpen(false);
  }

  if (authed === null) {
    return (
      <div className="landing">
        <div className="landing-bg" />
        <div className="landing-content">
          <p className="brand-mark">
            Simu<em>lacra</em>
          </p>
          <p className="landing-sub">Loading…</p>
        </div>
      </div>
    );
  }

  if (!authed) {
    return (
      <>
        <Landing
          prompt={prompt}
          busy={false}
          files={fixtureFiles}
          dataAttached={dataAttached}
          error={null}
          authed={false}
          onPrompt={setPrompt}
          onToggleData={() => setDataAttached((v) => !v)}
          onBuild={() => setProfileOpen(true)}
          onLogin={() => setProfileOpen(true)}
          onDismissError={() => undefined}
        />
        <ProfileManageModal
          open={profileOpen}
          locked={false}
          onClose={() => setProfileOpen(false)}
          user={null}
          tenants={[]}
          clerkEnabled={clerkEnabled}
          clerkAvailable={clerkAvailable}
          onUseClerk={onUseClerk}
          onAuthed={handleAuthed}
          onSignOut={handleSignOut}
          initialTab="auth"
        />
      </>
    );
  }

  function buildPrompt(): string {
    const parts: string[] = [];
    if (goal.trim()) parts.push(`Goal: ${goal.trim()}`);
    if (prompt.trim()) parts.push(prompt.trim());
    return parts.join("\n\n");
  }

  async function loadProject(id: string) {
    setBusy(true);
    setError(null);
    try {
      const snap = await getProject(id);
      setSnapshot(snap);
      if (snap.project.design_brief) setDesignBrief(snap.project.design_brief);
      setMode(snap.project.phase === "plan" ? "plan" : "workspace");
      setPreviewOpen(false);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load project");
    } finally {
      setBusy(false);
    }
  }

  async function handleStartPlanning() {
    if (!dataAttached) {
      setError("Attach a data room before planning.");
      return;
    }
    const text = buildPrompt();
    if (text.length < 3) return;

    setBusy(true);
    setError(null);
    try {
      const brief = {
        ...designBrief,
        product_name: designBrief.product_name || prompt.slice(0, 60),
        one_liner: designBrief.one_liner || prompt.slice(0, 120),
      };
      const snap = await createProject(text, goal || prompt.slice(0, 80), brief);
      setSnapshot(snap);
      setMode("plan");
      setInput("");
      setSidebarOpen(false);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start plan");
    } finally {
      setBusy(false);
    }
  }

  async function handleDesignBriefChange(next: DesignBrief) {
    setDesignBrief(next);
    if (!snapshot || mode !== "plan") return;
    try {
      const snap = await patchDesignBrief(snapshot.project.id, next);
      setSnapshot(snap);
    } catch {
      /* keep local brief; server sync optional */
    }
  }

  async function handlePlanSend() {
    const text = input.trim();
    if (!snapshot || !text) return;
    setBusy(true);
    setError(null);
    setInput("");
    try {
      const snap = await sendPlanChat(snapshot.project.id, text);
      setSnapshot(snap);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Plan chat failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!snapshot) return;
    setBusy(true);
    setError(null);
    try {
      const snap = await approveProject(snapshot.project.id);
      setSnapshot(snap);
      setMode("workspace");
      pollUntilIdle(snapshot.project.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
      setBusy(false);
    }
  }

  async function handleSend() {
    const text = input.trim();
    if (!snapshot || !text) return;
    setBusy(true);
    setError(null);
    setInput("");
    try {
      const snap = await sendChat(snapshot.project.id, text);
      setSnapshot(snap);
      if (snap.job_id || snap.job?.status === "running" || snap.project.job?.status === "running") {
        pollUntilIdle(snapshot.project.id);
      } else {
        setBusy(false);
        await refreshProjects();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Send failed");
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!snapshot) return;
    try {
      const snap = await cancelProjectJob(snapshot.project.id);
      setSnapshot(snap);
      stopPolling();
      setBusy(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Stop failed");
    }
  }

  async function handleRollback() {
    if (!snapshot) return;
    setBusy(true);
    setError(null);
    try {
      const snap = await rollbackProject(snapshot.project.id);
      setSnapshot(snap);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeploy() {
    if (!snapshot) return;
    setBusy(true);
    setError(null);
    try {
      const snap = await deployProject(snapshot.project.id);
      setSnapshot(snap);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deploy failed");
    } finally {
      setBusy(false);
    }
  }

  function handleNew() {
    stopPolling();
    setSnapshot(null);
    setMode("landing");
    setGoal("");
    setPrompt("");
    setDesignBrief(DEFAULT_DESIGN_BRIEF);
    setDataAttached(true);
    setInput("");
    setError(null);
    setPreviewOpen(false);
    setSidebarOpen(false);
    setBusy(false);
  }

  function openGovernance(from: AppMode = mode) {
    setGovReturn(from);
    setMode("governance");
  }

  if (mode === "governance") {
    return <GovernancePage onBack={() => setMode(govReturn)} />;
  }

  if (mode === "admin") {
    return <AdminPage onBack={() => setMode(govReturn)} />;
  }

  if (mode === "landing") {
    return (
      <>
        <div className="landing-fabs">
          {tenants.length > 1 && (
            <select
              className="tenant-select"
              value={tenants.find((t) => t.id === getTenantId())?.id || tenants[0]?.id || "default"}
              onChange={(e) => {
                setTenantId(e.target.value);
                refreshProjects();
              }}
            >
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
          <button type="button" className="gov-fab" onClick={() => openGovernance("landing")}>
            <Shield size={14} />
            Governance
          </button>
          <button
            type="button"
            className="gov-fab"
            onClick={() => {
              setGovReturn("landing");
              setMode("admin");
            }}
          >
            <Building2 size={14} />
            Admin
          </button>
        </div>
        <Landing
          prompt={prompt}
          busy={busy}
          files={fixtureFiles}
          dataAttached={dataAttached}
          error={error}
          authed
          onPrompt={setPrompt}
          onToggleData={() => setDataAttached((v) => !v)}
          onBuild={handleStartPlanning}
          onLogin={() => setProfileOpen(true)}
          onDismissError={() => setError(null)}
        />
        <ProfileManageModal
          open={profileOpen}
          onClose={() => setProfileOpen(false)}
          user={user}
          tenants={tenants}
          clerkEnabled={clerkEnabled}
          clerkAvailable={clerkAvailable}
          onUseClerk={onUseClerk}
          onAuthed={handleAuthed}
          onSignOut={handleSignOut}
        />
      </>
    );
  }

  if (!snapshot) return null;

  const agentVariant = mode === "plan" ? "plan" : "workspace";

  return (
    <div className="shell agent-layout">
      {sidebarOpen && (
        <Sidebar
          projects={projects}
          activeId={snapshot.project.id}
          files={projectFiles}
          focus="projects"
          collapsed={false}
          onNew={handleNew}
          onSelect={loadProject}
          onToggle={() => setSidebarOpen(false)}
        />
      )}

      <div className="agent-main">
        <AgentShell
          variant={agentVariant}
          snapshot={snapshot}
          files={projectFiles}
          input={input}
          busy={running}
          error={error}
          traces={traces}
          sidebarOpen={sidebarOpen}
          designBrief={designBrief}
          onDesignBrief={handleDesignBriefChange}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onInput={setInput}
          onSend={mode === "plan" ? handlePlanSend : handleSend}
          onApprove={mode === "plan" ? handleApprove : undefined}
          onCancel={running ? handleCancel : undefined}
          onOpenPreview={() => setPreviewOpen(true)}
          onGovernance={() => openGovernance(mode)}
          onRollback={mode === "workspace" ? handleRollback : undefined}
          onDismissError={() => setError(null)}
        />
        <StatusBar project={snapshot.project} apiOk={apiOk} />
      </div>

      <PreviewDrawer
        open={previewOpen}
        snapshot={snapshot}
        onClose={() => setPreviewOpen(false)}
        onRefresh={() => loadProject(snapshot.project.id)}
      />

      {snapshot.project.gates_status === "pass" && !snapshot.project.deployed && previewOpen && (
        <div className="deploy-float">
          <button type="button" className="deploy-btn" disabled={running} onClick={handleDeploy}>
            Approve deploy
          </button>
        </div>
      )}
    </div>
  );
}
