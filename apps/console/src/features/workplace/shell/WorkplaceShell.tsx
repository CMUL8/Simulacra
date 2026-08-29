import { useEffect, useRef, useState } from "react";

import { markWorkspaceAttentionRead, type AttentionItem, type MissionSummary, type WorkspaceEventStream } from "../../../api";
import { AttentionInbox } from "../attention/AttentionInbox";
import { MissionConversationWorkspace } from "../conversation/MissionConversationWorkspace";
import { MissionCreationFlow } from "../onboarding/MissionCreationFlow";
import { WorkList } from "../work/WorkList";
import type { AttentionFilter, MissionStateFilter, WorkplaceDestination } from "./contracts";
import { useAttention, useMissionSummaries } from "./useWorkplaceQuery";
import "./workplace.css";
import "../attention/attention.css";

const navigation: Array<{ id: WorkplaceDestination; label: string }> = [
  { id: "missions", label: "Missions" },
  { id: "needs-you", label: "Needs you" },
  { id: "work", label: "Work" },
  { id: "settings", label: "Settings" },
];

const stateLabels: Record<string, string> = {
  draft: "Draft",
  ready: "Ready",
  running: "Active",
  active: "Active",
  waiting_for_human: "Needs a human",
  needs_human: "Needs a human",
  blocked: "Needs a human",
  paused: "Paused",
  completed: "Completed",
  failed: "Stopped",
  stopped: "Stopped",
  archived: "Archived",
};

function destinationFromPath(pathname: string): WorkplaceDestination {
  if (pathname === "/needs-you") return "needs-you";
  if (pathname === "/work") return "work";
  if (pathname.startsWith("/settings")) return "settings";
  return "missions";
}

function normalized<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}

function replaceUrl(pathname: string, query: URLSearchParams) {
  const suffix = query.toString();
  window.history.replaceState(window.history.state, "", `${pathname}${suffix ? `?${suffix}` : ""}`);
}

function currentRelativeUrl(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function activitySummary(mission: MissionSummary): string {
  const active = `${mission.active_work_count} active ${mission.active_work_count === 1 ? "item" : "items"}`;
  if (!mission.needs_human_count) return active;
  return `${active} · ${mission.needs_human_count} need a human`;
}

export function WorkplaceShell({ attentionEnabled, conversationEnabled = false, filesEnabled = false, previewEnabled = false, sseEnabled = false, bootstrapEnabled = false, workspaceId = "", currentHumanId = "", eventStream, onSearch: _onSearch, onSettings }: {
  attentionEnabled: boolean;
  conversationEnabled?: boolean;
  filesEnabled?: boolean;
  previewEnabled?: boolean;
  sseEnabled?: boolean;
  bootstrapEnabled?: boolean;
  workspaceId?: string;
  currentHumanId?: string;
  eventStream?: WorkspaceEventStream;
  onSearch: () => void;
  onSettings: () => void;
}) {
  const [location, setLocation] = useState(() => window.location.href);
  const [readNotice, setReadNotice] = useState<"success" | "error" | null>(null);
  const settingsButton = useRef<HTMLButtonElement>(null);
  const previousDestination = useRef<WorkplaceDestination | null>(null);

  useEffect(() => {
    const changed = () => setLocation(window.location.href);
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);

  const url = new URL(location);
  const destination = destinationFromPath(url.pathname);
  const newMission = url.pathname === "/missions/new";
  const missionDetail = !newMission && url.pathname.startsWith("/missions/");
  const missionId = missionDetail ? decodeURIComponent(url.pathname.split("/")[2] || "") : "";
  const pathMissionView = url.pathname.split("/")[3];
  const queryMissionView = url.searchParams.get("tab");
  const missionView = normalized<"conversation" | "work" | "files">(
    pathMissionView || queryMissionView,
    filesEnabled ? ["conversation", "work", "files"] : ["conversation", "work"],
    "conversation",
  );
  const missionState = normalized<MissionStateFilter>(url.searchParams.get("state"), ["active", "all"], "active");
  const returnMissionState = normalized<MissionStateFilter>(
    typeof window.history.state?.workplaceMissionState === "string" ? window.history.state.workplaceMissionState : null,
    ["active", "all"],
    "active",
  );
  const missionFocusTarget = url.searchParams.get("item") || url.searchParams.get("focus") || url.searchParams.get("attention");
  const missionFocusAction = url.searchParams.get("action");
  const attentionFilter = normalized<AttentionFilter>(url.searchParams.get("filter"), ["actionable", "all"], "actionable");

  useEffect(() => {
    if (previousDestination.current === "settings" && destination !== "settings") {
      const timer = window.setTimeout(() => settingsButton.current?.focus(), 0);
      previousDestination.current = destination;
      return () => window.clearTimeout(timer);
    }
    previousDestination.current = destination;
  }, [destination]);

  useEffect(() => {
    if (newMission && !bootstrapEnabled) {
      replaceUrl("/missions", new URLSearchParams({ state: "active" }));
      setLocation(window.location.href);
      return;
    }
    if (!newMission && !missionDetail && destination === "missions" && url.searchParams.get("state") !== missionState) {
      replaceUrl("/missions", new URLSearchParams({ state: missionState }));
      setLocation(window.location.href);
    }
    if (destination === "needs-you" && url.searchParams.get("filter") !== attentionFilter) {
      replaceUrl("/needs-you", new URLSearchParams({ filter: attentionFilter }));
      setLocation(window.location.href);
    }
  }, [attentionFilter, bootstrapEnabled, destination, missionDetail, missionState, newMission, url.searchParams]);

  useEffect(() => {
    if (!missionDetail || filesEnabled || (pathMissionView !== "files" && queryMissionView !== "files")) return;
    window.history.replaceState(window.history.state, "", `/missions/${encodeURIComponent(missionId)}/conversation`);
    setLocation(window.location.href);
  }, [filesEnabled, missionDetail, missionId, pathMissionView, queryMissionView]);

  useEffect(() => {
    if (destination === "settings") onSettings();
  }, [destination, onSettings]);

  const missions = useMissionSummaries(missionState, destination === "missions" && !missionDetail && !newMission);
  const attention = useAttention(attentionFilter, attentionEnabled && destination === "needs-you");

  const updateLocation = () => setLocation(window.location.href);
  const navigate = (next: WorkplaceDestination) => {
    setReadNotice(null);
    if (next === "settings") {
      window.history.pushState({ workplaceReturnTo: currentRelativeUrl() }, "", "/settings/account");
      updateLocation();
      onSettings();
      return;
    }
    const path = next === "missions" ? "/missions" : `/${next}`;
    const query = next === "missions" ? "?state=active" : next === "needs-you" ? "?filter=actionable" : "";
    window.history.pushState({}, "", `${path}${query}`);
    updateLocation();
  };
  const setMissionState = (next: MissionStateFilter) => {
    window.history.pushState({}, "", `/missions?state=${next}`);
    updateLocation();
  };
  const openNewMission = () => {
    window.history.pushState({}, "", "/missions/new");
    updateLocation();
  };
  const setAttentionFilter = (next: AttentionFilter) => {
    window.history.pushState({}, "", `/needs-you?filter=${next}`);
    updateLocation();
  };
  const openAttention = (item: AttentionItem) => {
    setReadNotice(null);
    window.history.pushState({}, "", item.deep_link);
    updateLocation();
    if (item.read) return;
    void markWorkspaceAttentionRead(item.id, item.revision)
      .then(({ item: updated }) => {
        attention.updateItem(updated);
        setReadNotice("success");
      })
      .catch(() => setReadNotice("error"));
  };

  const heading = missionDetail
    ? "Mission selected"
    : destination === "needs-you"
      ? "Needs you"
      : destination === "missions"
        ? "Missions"
        : destination === "work"
          ? "Work"
          : "Settings";

  return <div className="workplace-shell">
    <aside className="workplace-rail" aria-label="Global navigation">
      <strong className="workplace-brand">Missions</strong>
      {navigation.map((item) => <button
        key={item.id}
        ref={item.id === "settings" ? settingsButton : undefined}
        className={`workplace-nav-target${destination === item.id ? " is-current" : ""}`}
        type="button"
        aria-current={destination === item.id ? "page" : undefined}
        onClick={() => navigate(item.id)}
      >{item.label}</button>)}
    </aside>
    <main className={`workplace-main${missionDetail && conversationEnabled ? " is-mission-detail" : ""}${newMission ? " is-new-mission" : ""}`} aria-label="Workplace">
      {missionDetail && conversationEnabled || newMission ? null : <header className="workplace-header">
        <div><p className="workplace-eyebrow">Workspace</p><h1>{heading}</h1></div>
        <div className="workplace-header-actions">
          {bootstrapEnabled ? <button className="workplace-new-mission" type="button" onClick={openNewMission}>New Mission</button> : null}
          <button type="button" disabled aria-label="Search Missions (coming soon)" title="Mission search is coming soon">Search</button>
        </div>
      </header>}

      {readNotice ? <p className={`workplace-notice is-${readNotice}`} role={readNotice === "error" ? "alert" : "status"}>
        {readNotice === "success"
          ? "This item was marked read. The Mission itself was not changed."
          : "This item is still unread. Try again from Needs you."}
      </p> : null}

      {destination === "missions" ? newMission && bootstrapEnabled ? <MissionCreationFlow
        workspaceId={workspaceId}
        humanId={currentHumanId}
        onComplete={(createdMissionId) => {
          window.history.replaceState({}, "", `/missions/${encodeURIComponent(createdMissionId)}/conversation`);
          updateLocation();
        }}
        onCancel={() => {
          window.history.pushState({}, "", "/missions?state=active");
          updateLocation();
        }}
      /> : missionDetail ? conversationEnabled && missionId ? <MissionConversationWorkspace
        key={missionId}
        missionId={missionId}
        activeView={missionView}
        currentHumanId={currentHumanId}
        liveEnabled={sseEnabled}
        eventStream={eventStream}
        focusTarget={missionFocusTarget}
        focusAction={missionFocusAction}
        filesEnabled={filesEnabled}
        previewEnabled={previewEnabled}
        onView={(view, focus) => {
          const query = focus ? `?focus=${encodeURIComponent(focus)}` : "";
          window.history.pushState(window.history.state, "", `/missions/${encodeURIComponent(missionId)}/${view}${query}`);
          updateLocation();
        }}
        onBack={() => {
          window.history.pushState({}, "", `/missions?state=${returnMissionState}`);
          updateLocation();
        }}
      /> : <section className="workplace-handoff" aria-label="Selected Mission">
        <p>This Mission is selected. Its conversation, work, and files will open here in the next workplace update.</p>
        <button className="workplace-more" type="button" onClick={() => navigate("missions")}>Back to Missions</button>
      </section> : <section aria-label="Mission list">
        <div className="workplace-filter" aria-label="Mission state">
          <button type="button" aria-pressed={missionState === "active"} className={missionState === "active" ? "is-current" : ""} onClick={() => setMissionState("active")}>Active</button>
          <button type="button" aria-pressed={missionState === "all"} className={missionState === "all" ? "is-current" : ""} onClick={() => setMissionState("all")}>All</button>
        </div>
        {missions.loading ? <p className="workplace-empty" role="status">Loading Missions…</p> : missions.error && !missions.data ? <div className="workplace-empty workplace-empty-state" role="alert"><strong>{missions.error}</strong><button type="button" onClick={missions.retry}>Retry</button></div> : <>
          {missions.data?.items.length ? <div className="mission-grid">{missions.data.items.map((mission) => <button
            key={mission.id}
            className="mission-summary-card"
            type="button"
            onClick={() => {
              window.history.pushState({ workplaceMissionState: missionState }, "", `/missions/${encodeURIComponent(mission.id)}/conversation`);
              updateLocation();
            }}
          >
            <p className="mission-card-state">{stateLabels[mission.public_state] || "In progress"}</p>
            <h2>{mission.title}</h2>
            <span className="mission-card-outcome">{mission.outcome_summary || "Outcome not described yet."}</span>
            <footer>
              <span>{mission.human_count} {mission.human_count === 1 ? "human" : "humans"} · {mission.agent_count} {mission.agent_count === 1 ? "agent" : "agents"}</span>
              <span>{activitySummary(mission)}</span>
            </footer>
          </button>)}</div> : <div className="workplace-empty workplace-empty-state"><strong>No Missions here yet.</strong><span>Set an outcome to give humans and agents a shared place to carry the work forward.</span></div>}
          {missions.error ? <div className="workplace-inline-error" role="alert"><span>{missions.error}</span><button type="button" onClick={missions.retry}>Retry loading more Missions</button></div> : null}
          {missions.data?.next_cursor ? <button className="workplace-more" type="button" disabled={missions.loadingMore} aria-label="Load more Missions" onClick={missions.loadMore}>{missions.loadingMore ? "Loading…" : "Load more"}</button> : null}
        </>}
      </section> : null}

      {destination === "needs-you" ? <section aria-label="Attention list">{attentionEnabled ? <>
        <div className="workplace-filter" aria-label="Attention filter">
          <button type="button" aria-pressed={attentionFilter === "actionable"} className={attentionFilter === "actionable" ? "is-current" : ""} onClick={() => setAttentionFilter("actionable")}>Actionable</button>
          <button type="button" aria-pressed={attentionFilter === "all"} className={attentionFilter === "all" ? "is-current" : ""} onClick={() => setAttentionFilter("all")}>All</button>
          <span className="attention-counts" aria-live="polite">{attention.data?.actionable_count || 0} actionable · {attention.data?.unread_count || 0} unread</span>
        </div>
        {attention.loading ? <p className="workplace-empty" role="status">Loading Needs you…</p> : attention.error && !attention.data ? <div className="workplace-empty workplace-empty-state" role="alert"><strong>{attention.error}</strong><button type="button" onClick={attention.retry}>Retry</button></div> : <>
          <AttentionInbox items={attention.data?.items || []} onOpen={openAttention} />
          {attention.error ? <div className="workplace-inline-error" role="alert"><span>{attention.error}</span><button type="button" onClick={attention.retry}>Retry loading more attention</button></div> : null}
          {attention.data?.next_cursor ? <button className="workplace-more" type="button" disabled={attention.loadingMore} aria-label="Load more attention" onClick={attention.loadMore}>{attention.loadingMore ? "Loading…" : "Load more"}</button> : null}
        </>}
      </> : <div className="workplace-empty workplace-empty-state"><strong>Needs you is not enabled yet.</strong><span>Your Missions are still available from the Missions view.</span></div>}</section> : null}

      {destination === "work" ? <WorkList /> : null}
      {destination === "settings" ? <div className="workplace-empty workplace-empty-state"><strong>Account settings are open.</strong><span>Manage your identity and workspace membership in the dialog.</span></div> : null}
    </main>
  </div>;
}
