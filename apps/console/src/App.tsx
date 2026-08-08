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
  getProjectJob,
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
import { AgentShell } from "./components/AgentShell";
import { Landing } from "./components/Landing";
import { PreviewDrawer } from "./components/PreviewDrawer";
import { ProfileManageModal, type ProfileTab } from "./components/ProfileManageModal";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { useEventStream } from "./hooks/useEventStream";

type AppMode = "landing" | "plan" | "workspace";

function jobRunning(snap: Snapshot | null, live = true): boolean {
  const status = snap?.job?.status ?? snap?.project.job?.status;
  // After deploy/restart, state.job can linger as "running" with no live worker
  if (!live && (status === "running" || status === "settling")) return false;
  return status === "running" || status === "settling";
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
  const [previewRefresh, setPreviewRefresh] = useState(0);
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
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileTab, setProfileTab] = useState<ProfileTab>("account");
  const [jobLive, setJobLive] = useState(false);
  const pollRef = useRef<number | null>(null);
  const busyStartedAt = useRef<number | null>(null);

  const projectId = snapshot?.project.id ?? null;
  const { events: traces } = useEventStream(projectId);
  const running = busy || jobRunning(snapshot, jobLive);

  useEffect(() => {
    if (running) {
      if (busyStartedAt.current == null) busyStartedAt.current = Date.now();
    } else {
      busyStartedAt.current = null;
    }
  }, [running]);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setAuthed(false);
      return;
    }
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      cancelled = true;
      clearAuth();
      setAuthed(false);
    }, 8000);
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        window.clearTimeout(timeout);
        setUser(me.user);
        setTenants(me.tenants || []);
        if (me.tenant_id) setTenantId(me.tenant_id);
        setAuthed(true);
      })
      .catch(() => {
        if (cancelled) return;
        window.clearTimeout(timeout);
        clearAuth();
        setAuthed(false);
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
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
      let ticks = 0;
      pollRef.current = window.setInterval(async () => {
        ticks += 1;
        try {
          const [snap, liveInfo] = await Promise.all([getProject(id), getProjectJob(id)]);
          setSnapshot(snap);
          setJobLive(Boolean(liveInfo.live));
          const status = liveInfo.job?.status ?? snap.job?.status ?? snap.project.job?.status ?? "idle";
          const liveRunning = liveInfo.live && (status === "running" || status === "settling");
          const staleRunning = !liveInfo.live && (status === "running" || status === "settling");
          const timedOut = ticks >= 80; // ~2 min at 1.5s
          if (!liveRunning || staleRunning || timedOut) {
            if (staleRunning || timedOut) {
              setBusy(false);
              setJobLive(false);
            } else {
              setBusy(false);
            }
            stopPolling();
            if (snap.project.phase === "ready") {
              setMode("workspace");
            } else {
              setMode("plan");
            }
            // Auto-open draft/build preview when a browser-reachable URL exists
            if (snap.preview_url && !String(snap.preview_url).includes("127.0.0.1")) {
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
          const status = snap.job?.status ?? snap.project.job?.status ?? "idle";
          if (status === "idle" || status === "failed" || status === "cancelled") {
            setBusy(false);
            stopPolling();
          }
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
          projects={[]}
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
      if (snap.job?.status === "running" || snap.project.job?.status === "running") {
        setJobLive(true);
        pollUntilIdle(snap.project.id);
      } else {
        setBusy(false);
        setJobLive(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start plan");
      setBusy(false);
    }
  }

  async function handleDesignBriefSave(next: DesignBrief) {
    setDesignBrief(next);
    if (!snapshot) throw new Error("No project");
    const snap = await patchDesignBrief(snapshot.project.id, next);
    setSnapshot(snap);
    if (snap.project.design_brief) setDesignBrief(snap.project.design_brief);
    // Style chips patch live dist — reload iframe so user sees the change now
    if (snap.preview_url) {
      setPreviewOpen(true);
      setPreviewRefresh((n) => n + 1);
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
      if (snap.job?.status === "running" || snap.project.job?.status === "running") {
        setJobLive(true);
        pollUntilIdle(snapshot.project.id);
      } else {
        setBusy(false);
        setJobLive(false);
        await refreshProjects();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Plan chat failed");
      setBusy(false);
      setJobLive(false);
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
      setJobLive(true);
      pollUntilIdle(snapshot.project.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
      setBusy(false);
      setJobLive(false);
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
        setJobLive(true);
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
    } catch (err) {
      // Soft-fail: still unlock UI even if cancel races with an already-idle job
      setError(err instanceof Error ? err.message : "Stop failed");
      try {
        const snap = await getProject(snapshot.project.id);
        setSnapshot(snap);
      } catch {
        /* ignore */
      }
    } finally {
      stopPolling();
      setBusy(false);
      setJobLive(false);
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

  function openAccount(tab: ProfileTab = "account") {
    setProfileTab(tab);
    setProfileOpen(true);
  }

  if (mode === "landing") {
    return (
      <>
        <Landing
          prompt={prompt}
          busy={busy}
          files={fixtureFiles}
          dataAttached={dataAttached}
          error={error}
          authed
          projects={projects}
          onPrompt={setPrompt}
          onToggleData={() => setDataAttached((v) => !v)}
          onBuild={handleStartPlanning}
          onOpenProject={loadProject}
          onLogin={() => openAccount("account")}
          onDismissError={() => setError(null)}
        />
        <ProfileManageModal
          open={profileOpen}
          onClose={() => setProfileOpen(false)}
          user={user}
          tenants={tenants}
          tenantId={tenants.find((t) => t.id === getTenantId())?.id || tenants[0]?.id}
          onTenant={(id) => {
            setTenantId(id);
            refreshProjects();
          }}
          clerkEnabled={clerkEnabled}
          clerkAvailable={clerkAvailable}
          onUseClerk={onUseClerk}
          onAuthed={handleAuthed}
          onSignOut={handleSignOut}
          initialTab={profileTab}
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
          onSaveDesignBrief={handleDesignBriefSave}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onInput={setInput}
          onSend={mode === "plan" ? handlePlanSend : handleSend}
          onApprove={handleApprove}
          onCancel={running ? handleCancel : undefined}
          onOpenPreview={() => setPreviewOpen(true)}
          onGovernance={() => openAccount("account")}
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
        onDeploy={handleDeploy}
        busy={running}
        refreshToken={previewRefresh}
      />

      <ProfileManageModal
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        user={user}
        tenants={tenants}
        tenantId={tenants.find((t) => t.id === getTenantId())?.id || tenants[0]?.id}
        onTenant={(id) => {
          setTenantId(id);
          refreshProjects();
        }}
        clerkEnabled={clerkEnabled}
        clerkAvailable={clerkAvailable}
        onUseClerk={onUseClerk}
        onAuthed={handleAuthed}
        onSignOut={handleSignOut}
        initialTab={profileTab}
      />
    </div>
  );
}
