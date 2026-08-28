import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  DEFAULT_DESIGN_BRIEF,
  approveProject,
  bootstrapMission,
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
  type WorkplaceFlags,
} from "./api";
import { AgentShell } from "./components/AgentShell";
import { CommandPalette, type PaletteItem } from "./components/CommandPalette";
import { Landing } from "./components/Landing";
import { MissionLoader } from "./components/MissionLoader";
import { PreviewDrawer } from "./components/PreviewDrawer";
import { ProfileManageModal, type ProfileTab } from "./components/ProfileManageModal";
import { Sidebar } from "./components/Sidebar";
import { ResizableSplit } from "./components/ui/ResizableSplit";
import { useEventStream } from "./hooks/useEventStream";
import { WorkplaceShell } from "./features/workplace/shell/WorkplaceShell";

type AppMode = "landing" | "plan" | "workspace";

const LANDING_DRAFT_KEY = "simulacra.landingDraft";
const SIDEBAR_KEY = "simulacra.sidebarOpen";
const WORKPLACE_FLAGS_OFF: WorkplaceFlags = {
  workplace_shell_v1: false,
  workplace_attention_v1: false,
  workplace_conversation_v1: false,
  workplace_files_v1: false,
  workplace_preview_origin_v1: false,
  workplace_sse_v1: false,
  workplace_bootstrap_v1: false,
};

function readSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const v = localStorage.getItem(SIDEBAR_KEY);
    if (v === "0") return false;
    if (v === "1") return window.innerWidth > 900;
  } catch {
    /* ignore */
  }
  return window.innerWidth > 900;
}

function writeSidebarOpen(open: boolean) {
  try {
    localStorage.setItem(SIDEBAR_KEY, open ? "1" : "0");
  } catch {
    /* ignore */
  }
}

type LandingDraft = {
  prompt: string;
  artifactKind: ArtifactKind;
  resumeBuild: boolean;
};

function jobRunning(snap: Snapshot | null, live = true): boolean {
  const status = snap?.job?.status ?? snap?.project.job?.status;
  // A persisted running status is not active work unless the status endpoint confirms it is live.
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
  authRequired = true,
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
}: {
  authRequired?: boolean;
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
}) {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [workplaceFlags, setWorkplaceFlags] = useState<WorkplaceFlags | null>(null);
  const [identityRevision, setIdentityRevision] = useState(0);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [mode, setMode] = useState<AppMode>("landing");
  const [sidebarOpen, setSidebarOpen] = useState(readSidebarOpen);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [previewRefresh, setPreviewRefresh] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [projectFiles, setProjectFiles] = useState<DataRoomFile[]>([]);
  const [goal, setGoal] = useState("");
  const [prompt, setPrompt] = useState("");
  const [artifactKind, setArtifactKind] = useState<ArtifactKind>("data_app");
  const [designBrief, setDesignBrief] = useState<DesignBrief>(DEFAULT_DESIGN_BRIEF);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
  const legacyRequestEpoch = useRef(0);
  const legacyAccessRef = useRef(false);
  const legacyWasEnabledRef = useRef(false);
  const [waitStartedAt, setWaitStartedAt] = useState<number | null>(null);
  const draftBootstrapped = useRef(false);
  const resumeStarted = useRef(false);

  const legacyEnabled = authed === true && workplaceFlags !== null && !workplaceFlags.workplace_shell_v1;
  legacyAccessRef.current = legacyEnabled;
  const projectId = legacyEnabled ? snapshot?.project.id ?? null : null;
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
    function onResize() {
      if (window.innerWidth <= 900) return;
      try {
        if (localStorage.getItem(SIDEBAR_KEY) !== "0") setSidebarOpen(true);
      } catch {
        setSidebarOpen(true);
      }
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!bgNotice) return;
    const t = window.setTimeout(() => setBgNotice(null), 4200);
    return () => window.clearTimeout(t);
  }, [bgNotice]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        if (!authed) return;
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (e.key === "Escape" && previewOpen && !paletteOpen && !profileOpen) {
        setPreviewOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [authed, previewOpen, paletteOpen, profileOpen]);

  useEffect(() => {
    if (draftBootstrapped.current) return;
    draftBootstrapped.current = true;
    const draft = readLandingDraft();
    if (!draft) return;
    setPrompt(draft.prompt);
    setArtifactKind(draft.artifactKind);
    if (draft.resumeBuild) {
      setResumeBuild(true);
      setGuestGateOpen(true);
    }
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token && authRequired) {
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
        setWorkplaceFlags(me.workplace_flags || WORKPLACE_FLAGS_OFF);
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
  }, [authRequired, identityRevision]);

  // Deep-link password reset: /#reset=spr_…
  useEffect(() => {
    const hash = window.location.hash || "";
    if (!/(?:^|#|&)reset=/.test(hash)) return;
    setAuthMode("login");
    setProfileTab("auth");
    setProfileOpen(true);
  }, []);

  const captureLegacyEpoch = useCallback((): number | null => (
    legacyAccessRef.current ? legacyRequestEpoch.current : null
  ), []);

  const isLegacyEpochCurrent = useCallback((epoch: number | null): epoch is number => (
    epoch !== null && legacyAccessRef.current && legacyRequestEpoch.current === epoch
  ), []);

  useEffect(() => {
    if (!workplaceFlags?.workplace_shell_v1) return;
    const syncSettingsRoute = () => {
      const isSettings = window.location.pathname.startsWith("/settings/") || window.location.pathname === "/settings";
      setProfileOpen(isSettings);
      if (isSettings) setProfileTab("account");
    };
    syncSettingsRoute();
    window.addEventListener("popstate", syncSettingsRoute);
    return () => window.removeEventListener("popstate", syncSettingsRoute);
  }, [workplaceFlags?.workplace_shell_v1]);

  const refreshProjects = useCallback(async () => {
    if (!legacyAccessRef.current) return;
    const epoch = legacyRequestEpoch.current;
    try {
      const next = await listProjects();
      if (legacyAccessRef.current && legacyRequestEpoch.current === epoch) setProjects(next);
    } catch {
      /* list failed — keep last known projects */
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

  const clearLegacyActivity = useCallback(() => {
    legacyRequestEpoch.current += 1;
    legacyAccessRef.current = false;
    stopPolling();
    viewedIdRef.current = null;
    setSnapshot(null);
    setProjectFiles([]);
    setProjects([]);
    setBusy(false);
    setJobLive(false);
    setBusyProjects({});
    setBgNotice(null);
    setPreviewOpen(false);
    setMode("landing");
  }, [stopPolling]);

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
      if (!legacyAccessRef.current) return;
      const epoch = legacyRequestEpoch.current;
      stopPolling(id);
      markProjectBusy(id, true);
      let ticks = 0;
      pollById.current[id] = window.setInterval(async () => {
        if (!legacyAccessRef.current || legacyRequestEpoch.current !== epoch) {
          stopPolling(id);
          return;
        }
        ticks += 1;
        try {
          const [snap, liveInfo] = await Promise.all([getProject(id), getProjectJob(id)]);
          if (!legacyAccessRef.current || legacyRequestEpoch.current !== epoch) {
            stopPolling(id);
            return;
          }
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

  useLayoutEffect(() => {
    if (legacyEnabled) {
      legacyWasEnabledRef.current = true;
      return;
    }
    if (!legacyWasEnabledRef.current) return;
    legacyWasEnabledRef.current = false;
    clearLegacyActivity();
  }, [clearLegacyActivity, legacyEnabled]);

  useEffect(() => {
    if (!legacyEnabled) return;
    refreshProjects();
  }, [legacyEnabled, refreshProjects]);

  // Keep sidebar activity markers in sync with list payloads
  useEffect(() => {
    setBusyProjects((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const p of projects) {
        const status = p.job?.status;
        if (isLiveJobStatus(status) && !next[p.id] && !pollById.current[p.id]) {
          // Persisted running state without a live poll must not flash as current activity.
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
    if (!legacyEnabled || !id) {
      setProjectFiles([]);
      return;
    }
    const epoch = legacyRequestEpoch.current;
    let cancelled = false;
    listProjectFiles(id).then((files) => {
      if (!cancelled && legacyAccessRef.current && legacyRequestEpoch.current === epoch) setProjectFiles(files);
    }).catch(() => {
      if (!cancelled && legacyAccessRef.current && legacyRequestEpoch.current === epoch) setProjectFiles([]);
    });
    return () => { cancelled = true; };
  }, [legacyEnabled, snapshot?.project.id]);

  // When SSE says done (or sources promoted), refresh snapshot + data room
  useEffect(() => {
    if (!legacyEnabled) return;
    const last = traces[traces.length - 1];
    if (!last || !projectId) return;
    const epoch = legacyRequestEpoch.current;
    const promoted =
      last.type === "phase" && /data room|sources/i.test(last.label || "");
    if (last.type === "done" || promoted || (last.type === "error" && last.status === "fail")) {
      getProject(projectId)
        .then((snap) => {
          if (!legacyAccessRef.current || legacyRequestEpoch.current !== epoch || viewedIdRef.current !== projectId) return;
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
        .then((files) => {
          if (legacyAccessRef.current && legacyRequestEpoch.current === epoch) setProjectFiles(files);
        })
        .catch(() => undefined);
    }
  }, [legacyEnabled, traces, projectId, markProjectBusy, stopPolling]);

  function handleSignOut() {
    const clerkOut = (window as unknown as { __simulacraClerkSignOut?: () => Promise<void> })
      .__simulacraClerkSignOut;
    clearLegacyActivity();
    clearAuth();
    setAuthed(false);
    setUser(null);
    setWorkplaceFlags(null);
    setTenants([]);
    setProfileOpen(false);
    setGuestGateOpen(false);
    setResumeBuild(false);
    resumeStarted.current = false;
    clearLandingDraft();
    if (clerkOut) void clerkOut();
  }

  function handleAuthed(session: AuthSession) {
    clearLegacyActivity();
    setUser(session.user);
    setTenants(session.tenants || []);
    setWorkplaceFlags(null);
    setAuthed(true);
    setProfileOpen(false);
    setGuestGateOpen(false);
    setIdentityRevision((value) => value + 1);
  }

  function switchWorkplaceTenant(id: string) {
    clearLegacyActivity();
    setTenantId(id);
    closeWorkplaceAccount();
    setWorkplaceFlags(null);
    setIdentityRevision((value) => value + 1);
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
      resumeBuild: resume,
    });
  }

  const handleStartPlanning = useCallback(async () => {
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
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
        artifactKind,
      });
      if (!isLegacyEpochCurrent(epoch)) return;
      if (pendingFiles.length > 0) {
        snap = await uploadProjectFiles(snap.project.id, pendingFiles, { reingest: true });
        if (!isLegacyEpochCurrent(epoch)) return;
        setPendingFiles([]);
      }
      const outcome = (goal || prompt).trim();
      const deliverable = artifactKind === "data_app"
        ? "working application"
        : artifactKind === "one_pager"
          ? "one-page brief"
          : artifactKind === "slides"
            ? "slide deck"
            : "report";
      if (!isLegacyEpochCurrent(epoch)) return;
      await bootstrapMission(snap.project.id, {
        title: snap.project.app_config?.title || prompt.slice(0, 80),
        objective: outcome,
        definition_of_done: `Produce a source-grounded ${deliverable}, resolve or clearly flag material exceptions, and obtain human verification of the exact final version.`,
      });
      if (!isLegacyEpochCurrent(epoch)) return;
      viewedIdRef.current = snap.project.id;
      setSnapshot(snap);
      setMode(snap.project.phase === "ready" ? "workspace" : "plan");
      setInput("");
      await refreshProjects();
      if (!isLegacyEpochCurrent(epoch)) return;
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
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Failed to start plan");
      setBusy(false);
    }
  }, [
    artifactKind,
    captureLegacyEpoch,
    designBrief,
    goal,
    pendingFiles,
    pollUntilIdle,
    markProjectBusy,
    isLegacyEpochCurrent,
    prompt,
    refreshProjects,
  ]);

  // After guest send → login, continue into the create flow with preserved draft.
  useEffect(() => {
    if (!legacyEnabled || !resumeBuild || busy) return;
    if (resumeStarted.current) return;
    if (prompt.trim().length < 3) {
      setResumeBuild(false);
      clearLandingDraft();
      return;
    }
    resumeStarted.current = true;
    void handleStartPlanning();
  }, [busy, handleStartPlanning, legacyEnabled, prompt, resumeBuild]);

  if (authed === null) {
    return (
      <div className="landing landing-boot">
        <div className="landing-content">
          <h1 className="boot-mark">Missions</h1>
          <MissionLoader label="Opening workspace" variant="matrix" className="landing-boot-status" />
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
          pendingFiles={pendingFiles}
          error={null}
          authed={false}
          projects={[]}
          guestGateOpen={guestGateOpen}
          clerkEnabled={clerkEnabled}
          onPrompt={setPrompt}
          onArtifactKind={setArtifactKind}
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
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
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
      if (!isLegacyEpochCurrent(epoch) || viewedIdRef.current !== id) return;
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
        if (!isLegacyEpochCurrent(epoch)) return;
        const files = await listProjectFiles(id);
        if (isLegacyEpochCurrent(epoch)) setProjectFiles(files);
      } catch {
        /* ignore */
      }
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
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
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    const projectId = snapshot.project.id;
    setBusy(true);
    setError(null);
    try {
      const snap = await approveProject(projectId);
      if (!isLegacyEpochCurrent(epoch)) return;
      setSnapshot(snap);
      setMode("workspace");
      setJobLive(true);
      markProjectBusy(projectId, true);
      pollUntilIdle(projectId);
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Build failed");
      setBusy(false);
      setJobLive(false);
      markProjectBusy(projectId, false);
    }
  }

  async function dispatchChat(text: string) {
    if (!snapshot || !text) return;
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    const project = snapshot.project;
    setBusy(true);
    markProjectBusy(project.id, true);
    setError(null);
    try {
      let snap: Snapshot;
      try {
        snap = await sendChat(project.id, text, project.active_chat_id);
        if (!isLegacyEpochCurrent(epoch)) return;
      } catch (first) {
        if (!isLegacyEpochCurrent(epoch)) return;
        // Stale chat id on older projects — retry against the project's healed active chat
        const msg = first instanceof Error ? first.message : String(first);
        if (/unknown chat/i.test(msg) && project.active_chat_id) {
          const fresh = await getProject(project.id);
          if (!isLegacyEpochCurrent(epoch)) return;
          setSnapshot(fresh);
          snap = await sendChat(fresh.project.id, text, fresh.project.active_chat_id);
          if (!isLegacyEpochCurrent(epoch)) return;
        } else {
          throw first;
        }
      }
      if (!isLegacyEpochCurrent(epoch)) return;
      setSnapshot(snap);
      if (snap.job_id || snap.job?.status === "running" || snap.project.job?.status === "running") {
        setJobLive(true);
        markProjectBusy(project.id, true);
        pollUntilIdle(project.id);
      } else {
        setBusy(false);
        markProjectBusy(project.id, false);
        await refreshProjects();
        if (!isLegacyEpochCurrent(epoch)) return;
      }
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Send failed");
      setBusy(false);
      markProjectBusy(project.id, false);
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
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    const id = snapshot.project.id;
    try {
      const snap = await cancelProjectJob(id);
      if (!isLegacyEpochCurrent(epoch)) return;
      if (viewedIdRef.current === id) setSnapshot(snap);
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      // Soft-fail: still unlock UI even if cancel races with an already-idle job
      setError(err instanceof Error ? err.message : "Stop failed");
      try {
        if (!isLegacyEpochCurrent(epoch)) return;
        const snap = await getProject(id);
        if (isLegacyEpochCurrent(epoch) && viewedIdRef.current === id) setSnapshot(snap);
      } catch {
        /* ignore */
      }
    } finally {
      if (!isLegacyEpochCurrent(epoch)) return;
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
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    const projectId = snapshot.project.id;
    setBusy(true);
    setError(null);
    try {
      const snap = await rollbackProject(projectId, checkpointId);
      if (!isLegacyEpochCurrent(epoch)) return;
      setSnapshot(snap);
      if (snap.preview_url) {
        setPreviewOpen(true);
        setPreviewRefresh((n) => n + 1);
      }
      await refreshProjects();
      if (!isLegacyEpochCurrent(epoch)) return;
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Restore failed");
    } finally {
      if (isLegacyEpochCurrent(epoch)) setBusy(false);
    }
  }

  async function handleDeploy() {
    if (!snapshot) return;
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    const projectId = snapshot.project.id;
    setBusy(true);
    setError(null);
    try {
      const snap = await deployProject(projectId);
      if (!isLegacyEpochCurrent(epoch)) return;
      setSnapshot(snap);
      await refreshProjects();
      if (!isLegacyEpochCurrent(epoch)) return;
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Deploy failed");
    } finally {
      if (isLegacyEpochCurrent(epoch)) setBusy(false);
    }
  }

  async function handleSelectChat(projectId: string, chatId: string) {
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    viewedIdRef.current = projectId;
    setError(null);
    try {
      const [snap, liveInfo] = await Promise.all([
        activateChat(projectId, chatId),
        getProjectJob(projectId),
      ]);
      if (!isLegacyEpochCurrent(epoch) || viewedIdRef.current !== projectId) return;
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
        if (!isLegacyEpochCurrent(epoch)) return;
        const files = await listProjectFiles(projectId);
        if (isLegacyEpochCurrent(epoch)) setProjectFiles(files);
      } catch {
        /* ignore */
      }
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Could not open chat");
    }
  }

  async function handleNewChat(projectId: string) {
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    viewedIdRef.current = projectId;
    setError(null);
    try {
      const snap = await createChat(projectId, { title: "Chat" });
      if (!isLegacyEpochCurrent(epoch) || viewedIdRef.current !== projectId) return;
      setSnapshot(snap);
      mergeProjectMeta(snap);
      setMode(snap.project.phase === "ready" ? "workspace" : "plan");
      setInput("");
      // Creating a chat must not wipe an in-flight job on this project
      const stillBusy = Boolean(busyProjects[projectId] || pollById.current[projectId]);
      setBusy(stillBusy);
      setJobLive(stillBusy);
      await refreshProjects();
      if (!isLegacyEpochCurrent(epoch)) return;
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
      setError(err instanceof Error ? err.message : "Could not create chat");
    }
  }

  async function handleDeleteChat(projectId: string, chatId: string) {
    const epoch = captureLegacyEpoch();
    if (epoch === null) return;
    setError(null);
    try {
      const snap = await deleteChat(projectId, chatId);
      if (!isLegacyEpochCurrent(epoch)) return;
      setSnapshot(snap);
      mergeProjectMeta(snap);
      await refreshProjects();
      if (!isLegacyEpochCurrent(epoch)) return;
    } catch (err) {
      if (!isLegacyEpochCurrent(epoch)) return;
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
    setInput("");
    setError(null);
    setPreviewOpen(false);
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

  function closeWorkplaceAccount() {
    setProfileOpen(false);
    if (!window.location.pathname.startsWith("/settings")) return;
    const state = window.history.state as { workplaceReturnTo?: unknown } | null;
    const returnTo = typeof state?.workplaceReturnTo === "string" && state.workplaceReturnTo.startsWith("/")
      ? state.workplaceReturnTo
      : "/missions?state=active";
    window.history.replaceState({}, "", returnTo);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }

  function persistSidebar(open: boolean) {
    setSidebarOpen(open);
    writeSidebarOpen(open);
  }

  const paletteItems: PaletteItem[] = [
    {
      id: "new",
      label: "New project",
      group: "Action",
      onSelect: handleNew,
    },
    ...(snapshot
      ? [
          {
            id: "preview",
            label: previewOpen ? "Hide preview" : "Show preview",
            group: "Action",
            disabled: !snapshot.preview_url,
            onSelect: () => setPreviewOpen((v) => !v),
          },
          {
            id: "chat",
            label: "New chat",
            group: "Action",
            onSelect: () => void handleNewChat(snapshot.project.id),
          },
        ]
      : []),
    {
      id: "account",
      label: "Account",
      group: "Action",
      onSelect: () => openAccount("account"),
    },
    ...projects.slice(0, 20).map((p) => ({
      id: `p-${p.id}`,
      label: p.app_config?.title || "Untitled",
      hint: p.phase === "ready" ? "Built" : p.phase === "plan" ? "Plan" : p.phase,
      group: "Project",
      onSelect: () => void loadProject(p.id),
    })),
  ];

  if (authed && workplaceFlags === null) {
    return (
      <div className="landing landing-boot">
        <div className="landing-content">
          <h1 className="boot-mark">Missions</h1>
          <MissionLoader label="Opening workspace" variant="matrix" className="landing-boot-status" />
        </div>
      </div>
    );
  }

  if (authed && workplaceFlags?.workplace_shell_v1) {
    return (
      <>
        <WorkplaceShell
          attentionEnabled={workplaceFlags.workplace_attention_v1}
          conversationEnabled={workplaceFlags.workplace_conversation_v1}
          filesEnabled={workplaceFlags.workplace_files_v1}
          previewEnabled={workplaceFlags.workplace_preview_origin_v1}
          sseEnabled={workplaceFlags.workplace_sse_v1}
          currentHumanId={user?.id || ""}
          onSearch={() => undefined}
          onSettings={() => openAccount("account")}
        />
        <ProfileManageModal
          open={profileOpen}
          onClose={closeWorkplaceAccount}
          user={user}
          tenants={tenants}
          tenantId={tenants.find((t) => t.id === getTenantId())?.id || tenants[0]?.id}
          onTenant={switchWorkplaceTenant}
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

  if (mode === "landing") {
    const bgBusyCount = Object.keys(busyProjects).length;
    return (
      <>
        <Landing
          prompt={prompt}
          artifactKind={artifactKind}
          busy={busy}
          busyProjectIds={busyProjects}
          pendingFiles={pendingFiles}
          error={error}
          authed
          projects={projects}
          onPrompt={setPrompt}
          onArtifactKind={setArtifactKind}
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
        <CommandPalette
          open={paletteOpen}
          items={paletteItems}
          onClose={() => setPaletteOpen(false)}
        />
        <ProfileManageModal
          open={profileOpen}
          onClose={() => setProfileOpen(false)}
          user={user}
          tenants={tenants}
          tenantId={tenants.find((t) => t.id === getTenantId())?.id || tenants[0]?.id}
          onTenant={switchWorkplaceTenant}
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
      {sidebarOpen ? (
        <>
          <button
            type="button"
            className="sidebar-scrim"
            aria-label="Close sidebar"
            onClick={() => persistSidebar(false)}
          />
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
            onNew={handleNew}
            onHome={handleNew}
            onAccount={() => openAccount("account")}
            onSelect={loadProject}
            onSelectChat={handleSelectChat}
            onNewChat={handleNewChat}
            onDeleteChat={handleDeleteChat}
            onSearch={() => setPaletteOpen(true)}
          />
        </>
      ) : null}

      <ResizableSplit
        sized="right"
        hidden={!previewOpen}
        defaultWidth={480}
        minWidth={300}
        maxWidth={720}
        left={
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
              onToggleSidebar={() => persistSidebar(!sidebarOpen)}
              onInput={setInput}
              onSend={handleSend}
              onRetry={handleRetry}
              onApprove={handleApprove}
              onRebuild={handleApprove}
              onCancel={running ? handleCancel : undefined}
              onOpenPreview={() => setPreviewOpen((v) => !v)}
              onGovernance={() => openAccount("account")}
              onRollback={mode === "workspace" ? handleRollback : undefined}
              onDismissError={() => setError(null)}
            />
          </div>
        }
        right={
          <PreviewDrawer
            open={previewOpen}
            snapshot={snapshot}
            onClose={() => setPreviewOpen(false)}
            onRefresh={() => loadProject(snapshot.project.id)}
            onDeploy={handleDeploy}
            busy={running}
            refreshToken={previewRefresh}
            previewEnabled={Boolean(workplaceFlags?.workplace_preview_origin_v1)}
            onAccessLost={clearLegacyActivity}
          />
        }
      />

      <CommandPalette
        open={paletteOpen}
        items={paletteItems}
        onClose={() => setPaletteOpen(false)}
      />

      <ProfileManageModal
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        user={user}
        tenants={tenants}
        tenantId={tenants.find((t) => t.id === getTenantId())?.id || tenants[0]?.id}
        onTenant={switchWorkplaceTenant}
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
