import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
	approveCurrentMissionPlan,
  createCmul8Invitation,
  createCmul8Room,
	decideMissionCheckpoint,
  getCmul8Room,
  heartbeatCmul8Presence,
  markCmul8InboxRead,
  reviewCmul8Task,
  claimCmul8Task,
  transitionCmul8Task,
	verifyMissionDeliverable,
  type Cmul8RoomPayload,
	type MissionOverview,
} from "../../api";
import type { ReviewDecision } from "../shared";
import type { DurableTaskState, MissionApprovalWork, MissionAssignment, MissionDeliverableWork, ProjectRoomFeatureAdapter, ProjectRoomPermissions, RoomMember } from "./contracts";
import { mapCmul8RoomPayload } from "./mapper";
import { ProjectRoom } from "./ProjectRoom";
import { TeamRoster } from "../team/TeamRoster";

const NO_PERMISSIONS: ProjectRoomPermissions = { manageTasks: false, reviewTasks: false, reviewGraph: false, handoff: false, invite: false, comment: false };
const ROOM_REFRESH_MS = 5_000;
const PRESENCE_HEARTBEAT_MS = 30_000;

function requestId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function commandDecision(decision: ReviewDecision): string {
  if (decision === "approved") return "approve";
  if (decision === "changes_requested") return "request_changes";
  return "reject";
}

function message(error: unknown): string {
  const raw = error instanceof Error ? error.message : "Mission request failed";
  if (/permission denied|errno 13/i.test(raw)) return "Mission storage is temporarily unavailable. Your work is safe; retry in a moment.";
  if (/not found/i.test(raw)) return "That Mission item is no longer available. The latest state has been loaded.";
  return raw.replace(/^Error:\s*/i, "");
}

export function ProjectRoomContainer({ projectId, missionAssignments = [], mission, onMissionRefresh, onOpenChat }: { projectId: string; missionAssignments?: MissionAssignment[]; mission?: MissionOverview | null; onMissionRefresh?: () => Promise<void>; onOpenChat?: () => void }) {
  const [payload, setPayload] = useState<Cmul8RoomPayload | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "forbidden">("loading");
  const [actionError, setActionError] = useState<string>();
  const [connectionState, setConnectionState] = useState<"connected" | "disconnected" | "unknown">("unknown");

  const load = useCallback(async (mode: "initial" | "manual" | "poll" = "manual"): Promise<boolean> => {
    if (mode !== "poll") setState("loading");
    try {
      let next: Cmul8RoomPayload;
      let created = false;
      try {
        next = await getCmul8Room(projectId);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
        next = await createCmul8Room(projectId);
        created = true;
      }
      setPayload(next);
      setState("ready");
      setConnectionState("connected");
      if (mode === "poll") setActionError(undefined);
      return created;
    } catch (error) {
      if (mode === "poll") {
        setConnectionState("disconnected");
        setActionError(`Live updates paused: ${message(error)}`);
        return false;
      }
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) setState("forbidden");
      else setState("error");
      setActionError(message(error));
      return false;
    }
  }, [projectId]);

  useEffect(() => {
    let disposed = false;
    let refreshTimer: number | undefined;
    let presenceTimer: number | undefined;
    setPayload(null); setState("loading"); setConnectionState("unknown");
    const refresh = async (mode: "initial" | "poll") => {
      if (disposed || document.visibilityState === "hidden") return;
      const created = await load(mode);
      if (created && !disposed) {
        try { await heartbeatCmul8Presence(projectId); } catch { /* The next heartbeat retries. */ }
        await load("poll");
      }
    };
    const heartbeat = async () => {
      if (disposed || document.visibilityState === "hidden") return;
      try { await heartbeatCmul8Presence(projectId); } catch { /* Presence is advisory. */ }
    };
    const stopTimers = () => {
      if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
      if (presenceTimer !== undefined) window.clearInterval(presenceTimer);
      refreshTimer = undefined;
      presenceTimer = undefined;
    };
    const startPolling = (initial = false) => {
      stopTimers();
      if (document.visibilityState !== "hidden") {
        void heartbeat();
        void refresh(initial ? "initial" : "poll");
        refreshTimer = window.setInterval(() => { void refresh("poll"); }, ROOM_REFRESH_MS);
        presenceTimer = window.setInterval(() => { void heartbeat(); }, PRESENCE_HEARTBEAT_MS);
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") stopTimers();
      else startPolling();
    };
    startPolling(true);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => { disposed = true; stopTimers(); document.removeEventListener("visibilitychange", onVisibilityChange); };
  }, [projectId, load]);

  const mapped = useMemo(() => payload ? mapCmul8RoomPayload(payload, connectionState) : null, [connectionState, payload]);
  const crewMembers = useMemo(() => {
    const roomMembers = (mapped?.room.members ?? []).filter((member): member is RoomMember & { kind: "human" | "agent" } => member.kind === "human" || member.kind === "agent");
    const roomIds = new Set(roomMembers.map((member) => member.id));
    const missionAgents: Array<RoomMember & { kind: "agent" }> = (mission?.agents ?? [])
      .filter((agent) => !roomIds.has(agent.id))
      .map((agent) => {
        const activeRun = mission?.runs.find((run) => run.current_agent_id === agent.id && run.status === "running");
        return { id: agent.id, name: agent.name, role: agent.role, kind: "agent", currentTask: activeRun?.trigger_snapshot?.note };
      });
    return [...missionAgents, ...roomMembers];
  }, [mapped, mission]);
	const missionApprovals = useMemo<MissionApprovalWork[]>(() => (mission?.approvals ?? []).map((approval) => {
		const run = mission?.runs.find((item) => item.id === approval.run_id);
		const pending = approval.status === "pending";
		return {
			id: approval.id,
			title: pending ? "Decision needed" : "Decision recorded",
			detail: pending ? run?.trigger_snapshot?.note || "Approve this checkpoint to continue the Mission." : `Human decision: ${approval.status.replaceAll("_", " ")}.`,
		status: pending ? "awaiting_approval" : ["approved", "consumed"].includes(approval.status) ? "done" : "closed",
			expectedRevision: approval.revision,
			expectedRunRevision: run?.revision ?? 0,
		};
	}), [mission]);
	const missionDeliverables = useMemo<MissionDeliverableWork[]>(() => (mission?.deliverables ?? []).map((deliverable) => ({ id: deliverable.id, title: deliverable.name, detail: deliverable.state === "verified" ? "Verified Mission output" : "Review the exact output before it is accepted.", status: deliverable.state === "verified" ? "done" : "ready_for_review", expectedVersion: deliverable.version })), [mission]);
  const mutate = useCallback(async (operation: () => Promise<unknown>) => {
    setActionError(undefined);
    try { await operation(); await load("poll"); } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await load("poll");
        setActionError("This room changed in another session. The latest state has been loaded; retry your action if it still applies.");
      } else setActionError(message(error));
    }
  }, [load]);

  const adapter = useMemo<Partial<ProjectRoomFeatureAdapter>>(() => ({
    addMember: async (memberEmail, role) => {
      if (!memberEmail.includes("@")) throw new Error("Enter the human's email address.");
      await mutate(() => createCmul8Invitation(projectId, { client_request_id: requestId("invite"), email: memberEmail, role }));
    },
    claimTask: async (taskId, revision) => { await mutate(() => claimCmul8Task(projectId, taskId, revision)); },
    transitionTask: async (taskId, next, revision) => {
      const current = mapped?.room.tasks.find((task) => task.id === taskId);
      await mutate(async () => {
        let expected = revision;
        if (current && !current.ownerId && current.durableState === "proposed") {
          const claimed = await claimCmul8Task(projectId, taskId, revision);
          expected = claimed.revision;
          if (next === "ready") return;
        }
        await transitionCmul8Task(projectId, taskId, next, expected);
      });
    },
    submitTaskReview: async (taskId, decision, note, revision) => { await mutate(() => reviewCmul8Task(projectId, taskId, commandDecision(decision), note ?? "", revision)); },
    markInboxRead: async (eventId) => { await mutate(() => markCmul8InboxRead(projectId, eventId)); },
    reconnect: async () => { await load("manual"); },
    approveGraph: async (revision) => { await mutate(() => approveCurrentMissionPlan(projectId, revision)); },
		decideMissionApproval: async (approvalId, decision, expectedRevision, expectedRunRevision) => { await mutate(async () => { await decideMissionCheckpoint(projectId, approvalId, decision, expectedRevision, expectedRunRevision); await onMissionRefresh?.(); }); },
		verifyMissionDeliverable: async (deliverableId, expectedVersion) => { await mutate(async () => { await verifyMissionDeliverable(projectId, deliverableId, expectedVersion); await onMissionRefresh?.(); }); },
  }), [load, mapped, mutate, onMissionRefresh, projectId]);

  const inviteHuman = useCallback(async (email: string, role: "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver") => {
    const created = await createCmul8Invitation(projectId, { client_request_id: requestId("invite"), email, role });
    const url = new URL(window.location.origin);
    url.searchParams.set("mission_id", projectId);
    url.searchParams.set("invitation_id", created.invitation.id);
    url.searchParams.set("invite_token", created.token);
    return { url: url.toString(), expiresAt: created.invitation.expires_at };
  }, [projectId]);

	return <div className="mission-workspace-layout">
    <ProjectRoom room={mapped?.room} permissions={mapped?.permissions ?? NO_PERMISSIONS} state={state} adapter={adapter} missionAssignments={missionAssignments} missionApprovals={missionApprovals} missionDeliverables={missionDeliverables} actionError={actionError} onOpenChat={onOpenChat} onRetryLoad={() => void load("manual")} />
    {state === "ready" && mapped?.room ? <TeamRoster members={crewMembers} canInvite={mapped.permissions.invite} onInviteMember={inviteHuman} /> : null}
  </div>;
}
