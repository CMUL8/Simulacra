import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_DESIGN_BRIEF,
  approveProject,
  cancelProjectJob,
  clearAuth,
  createProject,
  createChat,
  activateChat,
  deleteChat,
  deployProject,
  fetchMe,
  getProject,
  getProjectJob,
  getTenantId,
  getToken,
  listFixtureFiles,
  listProjectFiles,
  listProjects,
  rollbackProject,
  sendChat,
  setTenantId,
  uploadProjectFiles,
  type AuthSession,
  type AuthUser,
  type ArtifactKind,
  type DataRoomFile,
  type DesignBrief,
  type Project,
  type Snapshot,
  type Tenant,
} from "./api";
import { BG_IMAGES, bgPresetFromSearch } from "./bgPreset";
import { AgentShell } from "./components/AgentShell";
import { Landing } from "./components/Landing";
import { PreviewDrawer } from "./components/PreviewDrawer";
import { ProfileManageModal, type ProfileTab } from "./components/ProfileManageModal";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { useEventStream } from "./hooks/useEventStream";

type AppMode = "landing" | "plan" | "workspace";

const LANDING_DRAFT_KEY = "simulacra.landingDraft";

type LandingDraft = {
  prompt: string;
  artifactKind: ArtifactKind;
  dataAttached: boolean;
  resumeBuild: boolean;
};

function jobRunning(snap: Snapshot | null, live = true): boolean {
  const status = snap?.job?.status ?? snap?.project.job?.status;
  // After deploy/restart, state.job can linger as "running" with no live worker
  if (!live && (status === "running" || status === "settling")) return false;
  return status === "running" || status === "settling";
}

function isLiveJobStatus(status?: string | null): boolean {
  return status === "running" || status === "settling";
}

function readLandingDraft(): LandingDraft | null {
  try {
    const raw = sessionStorage.getItem(LANDING_DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LandingDraft>;
    if (typeof parsed.prompt !== "string") return null;
    return {
      prompt: parsed.prompt,
      artifactKind:
        parsed.artifactKind === "report" ||
        parsed.artifactKind === "slides" ||
        parsed.artifactKind === "one_pager" ||
        parsed.artifactKind === "data_app"
          ? parsed.artifactKind
          : "data_app",
      dataAttached: parsed.dataAttached === true,
      resumeBuild: Boolean(parsed.resumeBuild),
    };
  } catch {
    return null;
  }
}

function writeLandingDraft(draft: LandingDraft) {
  try {
    sessionStorage.setItem(LANDING_DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* ignore quota / private mode */
  }
}

function clearLandingDraft() {
  try {
    sessionStorage.removeItem(LANDING_DRAFT_KEY);
  } catch {
    /* ignore */
  }
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
  const [artifactKind, setArtifactKind] = useState<ArtifactKind>("data_app");
  const [designBrief, setDesignBrief] = useState<DesignBrief>(DEFAULT_DESIGN_BRIEF);
  const [dataAttached, setDataAttached] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiOk, setApiOk] = useState(true);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileTab, setProfileTab] = useState<ProfileTab>("account");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [guestGateOpen, setGuestGateOpen] = useState(false);
  const [resumeBuild, setResumeBuild] = useState(false);
  const [jobLive, setJobLive] = useState(false);
  const [busyProjects, setBusyProjects] = useState<Record<string, boolean>>({});
  const [bgNotice, setBgNotice] = useState<string | null>(null);
  const pollById = useRef<Record<string, number>>({});
  const viewedIdRef = useRef<string | null>(null);
  const [waitStartedAt, setWaitStartedAt] = useState<number | null>(null);
  const [sidebarFocusSearch, setSidebarFocusSearch] = useState(false);
  const draftBootstrapped = useRef(false);
  const resumeStarted = useRef(false);

  const projectId = snapshot?.project.id ?? null;
  viewedIdRef.current = projectId;
  const { events: traces } = useEventStream(projectId);
  const running = busy || jobRunning(snapshot, jobLive);

  useEffect(() => {
    if (running) {
      setWaitStartedAt((prev) => prev ?? Date.now());
    } else {
      setWaitStartedAt(null);
    }
  }, [running]);

  useEffect(() => {
    if (!bgNotice) return;
    const t = window.setTimeout(() => setBgNotice(null), 4200);
    return () => window.clearTimeout(t);
  }, [bgNotice]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "k") return;
      if (mode === "landing") return;
      e.preventDefault();
      setSidebarOpen(true);
      setSidebarFocusSearch(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  useEffect(() => {
    if (draftBootstrapped.current) return;
    draftBootstrapped.current = true;
    const draft = readLandingDraft();
    if (!draft) return;
    setPrompt(draft.prompt);
    setArtifactKind(draft.artifactKind);
    setDataAttached(draft.dataAttached);
    if (draft.resumeBuild) {
      setResumeBuild(true);
      setGuestGateOpen(true);
    }
  }, []);

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

  // Deep-link password reset: /#reset=spr_…
  useEffect(() => {
    const hash = window.location.hash || "";
    if (!/(?:^|#|&)reset=/.test(hash)) return;
    setAuthMode("login");
    setProfileTab("auth");
    setProfileOpen(true);
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await listProjects());
      setApiOk(true);
    } catch {
      setApiOk(false);
    }
  }, []);

  const stopPolling = useCallback((id?: string) => {
    if (id) {
      const handle = pollById.current[id];
      if (handle != null) {
        window.clearInterval(handle);
        delete pollById.current[id];
      }
      return;
    }
    for (const [pid, handle] of Object.entries(pollById.current)) {
      window.clearInterval(handle);
      delete pollById.current[pid];
    }
  }, []);

  const markProjectBusy = useCallback((id: string, on: boolean) => {
    setBusyProjects((prev) => {
      if (Boolean(prev[id]) === on) return prev;
      if (!on) {
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return { ...prev, [id]: true };
    });
  }, []);

  const pollUntilIdle = useCallback(
    (id: string) => {
      stopPolling(id);
      markProjectBusy(id, true);
      let ticks = 0;
      pollById.current[id] = window.setInterval(async () => {
        ticks += 1;
        try {
          const [snap, liveInfo] = await Promise.all([getProject(id), getProjectJob(id)]);
          const status = liveInfo.job?.status ?? snap.job?.status ?? snap.project.job?.status ?? "idle";
          const liveRunning = Boolean(liveInfo.live) && isLiveJobStatus(status);
          const staleRunning = !liveInfo.live && isLiveJobStatus(status);
          const timedOut = ticks >= 280; // ~7 min — create now includes builder
          const done = !liveRunning || staleRunning || timedOut;
          const viewing = viewedIdRef.current === id;

          if (viewing) {
            setSnapshot(snap);
            setJobLive(Boolean(liveInfo.live) && liveRunning);
          }

          if (done) {
            const title =
              snap.project.app_config?.title ||
              snap.project.prompt?.slice(0, 40) ||
              "Project";
            markProjectBusy(id, false);
            stopPolling(id);
            if (viewing) {
              setBusy(false);
              setJobLive(false);
              if (snap.project.phase === "ready") {
                setMode("workspace");
              } else {
                setMode("plan");
              }
              if (snap.preview_url && !String(snap.preview_url).includes("127.0.0.1")) {
                setPreviewOpen(true);
              }
            } else if (!staleRunning && !timedOut) {
              setBgNotice(`${title} finished in the background`);
            }
            await refreshProjects();
          } else {
            markProjectBusy(id, true);
          }
        } catch {
          /* keep polling briefly */
        }
      }, 1500);
    },
    [markProjectBusy, refreshProjects, stopPolling],
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  useEffect(() => {
    if (!authed) return;
    refreshProjects();
    listFixtureFiles().then(setFixtureFiles).catch(() => setFixtureFiles([]));
  }, [refreshProjects, authed]);

  // Keep sidebar activity markers in sync with list payloads
  useEffect(() => {
    setBusyProjects((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const p of projects) {
        const status = p.job?.status;
        if (isLiveJobStatus(status) && !next[p.id] && !pollById.current[p.id]) {
          // Stale "running" in state without a live poll — don't flash forever
          continue;
        }
        if (!isLiveJobStatus(status) && next[p.id] && !pollById.current[p.id]) {
          delete next[p.id];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [projects]);

  useEffect(() => {
    const id = snapshot?.project.id;
    if (!id) {
      setProjectFiles(fixtureFiles);
      return;
    }
    listProjectFiles(id).then(setProjectFiles).catch(() => setProjectFiles(fixtureFiles));
  }, [snapshot?.project.id, fixtureFiles]);

  // When SSE says done (or sources promoted), refresh snapshot + data room
  useEffect(() => {
    const last = traces[traces.length - 1];
    if (!last || !projectId) return;
    const promoted =
      last.type === "phase" && /data room|sources/i.test(last.label || "");
    if (last.type === "done" || promoted || (last.type === "error" && last.status === "fail")) {
      getProject(projectId)
        .then((snap) => {
          if (viewedIdRef.current !== projectId) return;
          setSnapshot(snap);
          const status = snap.job?.status ?? snap.project.job?.status ?? "idle";
          if (status === "idle" || status === "failed" || status === "cancelled") {
            setBusy(false);
            markProjectBusy(projectId, false);
            stopPolling(projectId);
          }
          if (snap.project.phase === "ready") {
            setMode("workspace");
            setBusy(false);
            markProjectBusy(projectId, false);
            stopPolling(projectId);
          }
        })
        .catch(() => undefined);
      listProjectFiles(projectId)
        .then(setProjectFiles)
        .catch(() => undefined);
    }
  }, [traces, projectId, markProjectBusy, stopPolling]);

  function handleSignOut() {
    const clerkOut = (window as unknown as { __simulacraClerkSignOut?: () => Promise<void> })
      .__simulacraClerkSignOut;
    clearAuth();
    setAuthed(false);
    setUser(null);
    setTenants([]);
    setProfileOpen(false);
    setGuestGateOpen(false);
    setResumeBuild(false);
    resumeStarted.current = false;
    clearLandingDraft();
    if (clerkOut) void clerkOut();
  }

  function handleAuthed(session: AuthSession) {
    setUser(session.user);
    setTenants(session.tenants || []);
    setAuthed(true);
    setProfileOpen(false);
    setGuestGateOpen(false);
  }

  function openGuestAuth(mode: "login" | "register") {
    setAuthMode(mode);
    setProfileTab("auth");
    setProfileOpen(true);
  }

  function saveGuestDraft(resume: boolean) {
    writeLandingDraft({
      prompt,
      artifactKind,
      dataAttached,
      resumeBuild: resume,
    });
  }

  const handleStartPlanning = useCallback(async () => {
    const parts: string[] = [];
    if (goal.trim()) parts.push(`Goal: ${goal.trim()}`);
    if (prompt.trim()) parts.push(prompt.trim());
    const text = parts.join("\n\n");
    if (text.length < 3) return;

    setBusy(true);
    setError(null);
    setResumeBuild(false);
    setGuestGateOpen(false);
    clearLandingDraft();
    try {
      const brief = {
        ...designBrief,
        product_name: designBrief.product_name || prompt.slice(0, 60),
        one_liner: designBrief.one_liner || prompt.slice(0, 120),
      };
      let snap = await createProject(text, goal || prompt.slice(0, 80), brief, {
        useFixture: dataAttached,
        artifactKind,
      });
      if (pendingFiles.length > 0) {
        snap = await uploadProjectFiles(snap.project.id, pendingFiles, { reingest: true });
        setPendingFiles([]);
      }
      setSnapshot(snap);
      setMode(snap.project.phase === "ready" ? "workspace" : "plan");
      setInput("");
      setSidebarOpen(false);
      await refreshProjects();
      if (snap.job?.status === "running" || snap.project.job?.status === "running") {
        setJobLive(true);
        markProjectBusy(snap.project.id, true);
        pollUntilIdle(snap.project.id);
      } else {
        setBusy(false);
        setJobLive(false);
        if (snap.project.phase === "ready") setMode("workspace");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start plan");
      setBusy(false);
    }
  }, [
    artifactKind,
    dataAttached,
    designBrief,
    goal,
    pendingFiles,
    pollUntilIdle,
    markProjectBusy,
    prompt,
    refreshProjects,
  ]);

  // After guest send → login, continue into the create flow with preserved draft.
  useEffect(() => {
    if (authed !== true || !resumeBuild || busy) return;
    if (resumeStarted.current) return;
    if (prompt.trim().length < 3) {
      setResumeBuild(false);
      clearLandingDraft();
      return;
    }
    resumeStarted.current = true;
    void handleStartPlanning();
  }, [authed, busy, handleStartPlanning, prompt, resumeBuild]);

  if (authed === null) {
    const bg = bgPresetFromSearch();
    return (
      <div className="landing landing-boot" data-bg={bg}>
        <img className="landing-hero-img" src={BG_IMAGES[bg]} alt="" aria-hidden />
        <div className="landing-hero-veil" aria-hidden />
        <div className="landing-content">
          <h1 className="brand-mark">
            Simu<em>lacra</em>
          </h1>
          <p className="landing-boot-status">Opening…</p>
        </div>
      </div>
    );
  }

  if (!authed) {
    return (
      <>
        <Landing
          prompt={prompt}
          artifactKind={artifactKind}
          busy={false}
          files={fixtureFiles}
          pendingFiles={pendingFiles}
          dataAttached={dataAttached}
          error={null}
          authed={false}
          projects={[]}
          guestGateOpen={guestGateOpen}
          clerkEnabled={clerkEnabled}
          onPrompt={setPrompt}
          onArtifactKind={setArtifactKind}
          onToggleData={() => setDataAttached((v) => !v)}
          onPickPending={(files) =>
            setPendingFiles((prev) => {
              const names = new Set(prev.map((f) => f.name));
              return [...prev, ...files.filter((f) => !names.has(f.name))];
            })
          }
          onClearPending={(name) => setPendingFiles((prev) => prev.filter((f) => f.name !== name))}
          onBuild={() => {
            resumeStarted.current = false;
            setGuestGateOpen(true);
            setResumeBuild(true);
            saveGuestDraft(true);
          }}
          onLogin={() => openGuestAuth("login")}
          onGuestCreateAccount={() => openGuestAuth("register")}
          onGuestSignIn={() => openGuestAuth("login")}
          onGuestGateDismiss={() => {
            setGuestGateOpen(false);
            setResumeBuild(false);
            resumeStarted.current = false;
            clearLandingDraft();
          }}
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
          authMode={authMode}
        />
      </>
    );
  }

  async function loadProject(id: string) {
    // Switching projects must not cancel background jobs or clear other polls.
    const leavingId = viewedIdRef.current;
    if (leavingId && leavingId !== id && (busyProjects[leavingId] || pollById.current[leavingId])) {
      const leaving = projects.find((p) => p.id === leavingId);
      const name = leaving?.app_config?.title || "Previous project";
      setBgNotice(`${name} keeps working in the background`);
    }
    viewedIdRef.current = id;
    setError(null);
    setInput("");
    setPreviewOpen(false);
    try {
      const [snap, liveInfo] = await Promise.all([getProject(id), getProjectJob(id)]);
      if (viewedIdRef.current !== id) return; // raced with another switch
      setSnapshot(snap);
      if (snap.project.design_brief) setDesignBrief(snap.project.design_brief);
      setMode(snap.project.phase === "plan" ? "plan" : "workspace");
      setProjects((prev) =>
        prev.map((p) =>
          p.id === snap.project.id
            ? {
                ...p,
                ...snap.project,
                chat: [],
                chat_index: snap.project.chat_index || snap.project.chats,
                active_chat_id: snap.project.active_chat_id,
              }
            : p,
        ),
      );
      const status = liveInfo.job?.status ?? snap.job?.status ?? snap.project.job?.status ?? "idle";
      const liveRunning = Boolean(liveInfo.live) && isLiveJobStatus(status);
      setJobLive(liveRunning);
      setBusy(liveRunning);
      if (liveRunning) {
        markProjectBusy(id, true);
        if (!pollById.current[id]) pollUntilIdle(id);
      } else {
        markProjectBusy(id, false);
      }
      try {
        setProjectFiles(await listProjectFiles(id));
      } catch {
        /* ignore */
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load project");
      setBusy(false);
      setJobLive(false);
    }
  }

  function mergeProjectMeta(snap: Snapshot) {
    setProjects((prev) =>
      prev.map((p) =>
        p.id === snap.project.id
          ? {
              ...p,
              app_config: snap.project.app_config,
              phase: snap.project.phase,
              status: snap.project.status,
              chat_index: snap.project.chat_index || snap.project.chats,
              active_chat_id: snap.project.active_chat_id,
              artifact_kind: snap.project.artifact_kind,
            }
          : p,
      ),
    );
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
      markProjectBusy(snapshot.project.id, true);
      pollUntilIdle(snapshot.project.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
      setBusy(false);
      setJobLive(false);
      markProjectBusy(snapshot.project.id, false);
    }
  }

  async function dispatchChat(text: string) {
    if (!snapshot || !text) return;
    setBusy(true);
    markProjectBusy(snapshot.project.id, true);
    setError(null);
    try {
      let snap;
      try {
        snap = await sendChat(snapshot.project.id, text, snapshot.project.active_chat_id);
      } catch (first) {
        // Stale chat id on older projects — retry against the project's healed active chat
        const msg = first instanceof Error ? first.message : String(first);
        if (/unknown chat/i.test(msg) && snapshot.project.active_chat_id) {
          const fresh = await getProject(snapshot.project.id);
          setSnapshot(fresh);
          snap = await sendChat(fresh.project.id, text, fresh.project.active_chat_id);
        } else {
          throw first;
        }
      }
      setSnapshot(snap);
      if (snap.job_id || snap.job?.status === "running" || snap.project.job?.status === "running") {
        setJobLive(true);
        markProjectBusy(snapshot.project.id, true);
        pollUntilIdle(snapshot.project.id);
      } else {
        setBusy(false);
        markProjectBusy(snapshot.project.id, false);
        await refreshProjects();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Send failed");
      setBusy(false);
      markProjectBusy(snapshot.project.id, false);
    }
  }

  async function handleSend() {
    const text = input.trim();
    if (!snapshot || !text) return;
    setInput("");
    await dispatchChat(text);
  }

  async function handleRetry(text: string) {
    const trimmed = text.trim();
    if (!snapshot || !trimmed || running) return;
    await dispatchChat(trimmed);
  }

  async function handleCancel() {
    if (!snapshot) return;
    const id = snapshot.project.id;
    try {
      const snap = await cancelProjectJob(id);
      if (viewedIdRef.current === id) setSnapshot(snap);
    } catch (err) {
      // Soft-fail: still unlock UI even if cancel races with an already-idle job
      setError(err instanceof Error ? err.message : "Stop failed");
      try {
        const snap = await getProject(id);
        if (viewedIdRef.current === id) setSnapshot(snap);
      } catch {
        /* ignore */
      }
    } finally {
      stopPolling(id);
      markProjectBusy(id, false);
      if (viewedIdRef.current === id) {
        setBusy(false);
        setJobLive(false);
      }
    }
  }

  async function handleRollback(checkpointId?: string) {
    if (!snapshot) return;
    setBusy(true);
    setError(null);
    try {
      const snap = await rollbackProject(snapshot.project.id, checkpointId);
      setSnapshot(snap);
      if (snap.preview_url) {
        setPreviewOpen(true);
        setPreviewRefresh((n) => n + 1);
      }
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Restore failed");
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

  async function handleSelectChat(projectId: string, chatId: string) {
    viewedIdRef.current = projectId;
    setError(null);
    try {
      const [snap, liveInfo] = await Promise.all([
        activateChat(projectId, chatId),
        getProjectJob(projectId),
      ]);
      if (viewedIdRef.current !== projectId) return;
      setSnapshot(snap);
      mergeProjectMeta(snap);
      setMode(snap.project.phase === "ready" ? "workspace" : "plan");
      const status = liveInfo.job?.status ?? snap.job?.status ?? snap.project.job?.status ?? "idle";
      const liveRunning = Boolean(liveInfo.live) && isLiveJobStatus(status);
      setJobLive(liveRunning);
      setBusy(liveRunning);
      if (liveRunning) {
        markProjectBusy(projectId, true);
        if (!pollById.current[projectId]) pollUntilIdle(projectId);
      }
      try {
        setProjectFiles(await listProjectFiles(projectId));
      } catch {
        /* ignore */
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open chat");
    }
  }

  async function handleNewChat(projectId: string) {
    viewedIdRef.current = projectId;
    setError(null);
    try {
      const snap = await createChat(projectId, { title: "Chat" });
      if (viewedIdRef.current !== projectId) return;
      setSnapshot(snap);
      mergeProjectMeta(snap);
      setMode(snap.project.phase === "ready" ? "workspace" : "plan");
      setInput("");
      setSidebarOpen(true);
      // Creating a chat must not wipe an in-flight job on this project
      const stillBusy = Boolean(busyProjects[projectId] || pollById.current[projectId]);
      setBusy(stillBusy);
      setJobLive(stillBusy);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create chat");
    }
  }

  async function handleDeleteChat(projectId: string, chatId: string) {
    setError(null);
    try {
      const snap = await deleteChat(projectId, chatId);
      setSnapshot(snap);
      mergeProjectMeta(snap);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete chat");
    }
  }

  function handleNew() {
    // Leave the workspace — do NOT kill background jobs / polls.
    const leavingId = viewedIdRef.current;
    if (leavingId && (busyProjects[leavingId] || pollById.current[leavingId])) {
      const leaving = projects.find((p) => p.id === leavingId);
      const name = leaving?.app_config?.title || "Project";
      setBgNotice(`${name} keeps working in the background`);
    }
    viewedIdRef.current = null;
    setSnapshot(null);
    setMode("landing");
    setGoal("");
    setPrompt("");
    setArtifactKind("data_app");
    setDesignBrief(DEFAULT_DESIGN_BRIEF);
    setDataAttached(true);
    setInput("");
    setError(null);
    setPreviewOpen(false);
    setSidebarOpen(false);
    setBusy(false);
    setJobLive(false);
    clearLandingDraft();
    setResumeBuild(false);
    setGuestGateOpen(false);
    resumeStarted.current = false;
  }

  function openAccount(tab: ProfileTab = "account") {
    setProfileTab(tab);
    setProfileOpen(true);
  }

  if (mode === "landing") {
    const bgBusyCount = Object.keys(busyProjects).length;
    return (
      <>
        <Landing
          prompt={prompt}
          artifactKind={artifactKind}
          busy={busy}
          busyProjectIds={busyProjects}
          files={fixtureFiles}
          pendingFiles={pendingFiles}
          dataAttached={dataAttached}
          error={error}
          authed
          projects={projects}
          onPrompt={setPrompt}
          onArtifactKind={setArtifactKind}
          onToggleData={() => setDataAttached((v) => !v)}
          onPickPending={(files) =>
            setPendingFiles((prev) => {
              const names = new Set(prev.map((f) => f.name));
              return [...prev, ...files.filter((f) => !names.has(f.name))];
            })
          }
          onClearPending={(name) => setPendingFiles((prev) => prev.filter((f) => f.name !== name))}
          onBuild={handleStartPlanning}
          onOpenProject={loadProject}
          onLogin={() => openAccount("account")}
          onDismissError={() => setError(null)}
        />
        {bgBusyCount > 0 || bgNotice ? (
          <div className="bg-job-toast" role="status">
            {bgNotice ||
              `${bgBusyCount} project${bgBusyCount === 1 ? "" : "s"} working in the background`}
            <button type="button" onClick={() => setBgNotice(null)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ) : null}
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
          activeChatId={snapshot.project.active_chat_id}
          busyProjectIds={busyProjects}
          files={projectFiles}
          focus="projects"
          collapsed={false}
          user={user}
          workspaceLabel={
            tenants.find((t) => t.id === getTenantId())?.name || tenants[0]?.name || "Workspace"
          }
          focusSearch={sidebarFocusSearch}
          onFocusSearchConsumed={() => setSidebarFocusSearch(false)}
          onNew={handleNew}
          onHome={handleNew}
          onAccount={() => openAccount("account")}
          onSelect={loadProject}
          onSelectChat={handleSelectChat}
          onNewChat={handleNewChat}
          onDeleteChat={handleDeleteChat}
          onToggle={() => setSidebarOpen(false)}
        />
      )}

      <div className="agent-main">
        {bgNotice ? (
          <div className="bg-job-toast nest" role="status">
            {bgNotice}
            <button type="button" onClick={() => setBgNotice(null)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ) : null}
        <AgentShell
          variant={agentVariant}
          snapshot={snapshot}
          files={projectFiles}
          input={input}
          busy={running}
          error={error}
          traces={traces}
          sidebarOpen={sidebarOpen}
          waitStartedAt={waitStartedAt}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onInput={setInput}
          onSend={handleSend}
          onRetry={handleRetry}
          onApprove={handleApprove}
          onRebuild={handleApprove}
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
