import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  addCmul8Comment,
  addCmul8RoomMember,
  approveCmul8Graph,
  createCmul8Room,
  getCmul8Room,
  heartbeatCmul8Presence,
  markCmul8InboxRead,
  reviewCmul8Task,
  claimCmul8Task,
  transitionCmul8Task,
  type Cmul8RoomPayload,
} from "../../api";
import type { GraphComment } from "../operation-graph";
import type { ReviewDecision } from "../shared";
import type { DurableTaskState, MissionAssignment, ProjectRoomFeatureAdapter, ProjectRoomPermissions } from "./contracts";
import { mapCmul8RoomPayload } from "./mapper";
import { ProjectRoom } from "./ProjectRoom";

const NO_PERMISSIONS: ProjectRoomPermissions = { manageTasks: false, reviewTasks: false, reviewGraph: false, handoff: false, invite: false, comment: false };
const ROOM_REFRESH_MS = 5_000;

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

function mentions(body: string): Array<{ ref_type: string; ref_id: string }> {
  return [...new Set([...body.matchAll(/(?:^|\s)@([A-Za-z0-9][A-Za-z0-9_.-]{0,127})/g)].map((match) => match[1]!))].map((ref_id) => ({ ref_type: "actor", ref_id }));
}

export function ProjectRoomContainer({ projectId, missionAssignments = [], onOpenChat }: { projectId: string; missionAssignments?: MissionAssignment[]; onOpenChat?: () => void }) {
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
    let timer: number | undefined;
    setPayload(null); setState("loading"); setConnectionState("unknown");
    const refresh = async (mode: "initial" | "poll") => {
      if (disposed || document.visibilityState === "hidden") return;
      try { await heartbeatCmul8Presence(projectId); } catch { /* GET below determines room availability. */ }
      const created = await load(mode);
      if (created && !disposed) {
        try { await heartbeatCmul8Presence(projectId); } catch { /* Next poll retries presence. */ }
        await load("poll");
      }
    };
    const startPolling = (initial = false) => {
      if (timer !== undefined) window.clearInterval(timer);
      if (document.visibilityState !== "hidden") {
        void refresh(initial ? "initial" : "poll");
        timer = window.setInterval(() => { void refresh("poll"); }, ROOM_REFRESH_MS);
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        if (timer !== undefined) window.clearInterval(timer);
        timer = undefined;
      } else startPolling();
    };
    startPolling(true);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => { disposed = true; if (timer !== undefined) window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibilityChange); };
  }, [projectId, load]);

  const mapped = useMemo(() => payload ? mapCmul8RoomPayload(payload, connectionState) : null, [connectionState, payload]);
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
    addMember: async (memberEmailOrId, role, revision) => { await mutate(() => addCmul8RoomMember(projectId, { ...(memberEmailOrId.includes("@") ? { member_email: memberEmailOrId } : { member_id: memberEmailOrId }), role, expected_revision: revision })); },
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
    approveGraph: async (revisionHash) => { await mutate(() => approveCmul8Graph(projectId, revisionHash)); },
    addComment: async (revisionId, body, section): Promise<GraphComment> => {
      const created = await addCmul8Comment(projectId, { body, target_type: "graph_element", target_id: revisionId, graph_revision: revisionId, graph_path: section?.startsWith("/") ? section : `/review/${section ?? "general"}`, mentions: mentions(body) });
      await load();
      return { id: created.id, author: created.author_id, body: created.body, createdAt: created.created_at, resolved: false, mentions: created.mentions?.map((item) => `${item.ref_type}:${item.ref_id}`), section: created.graph_path?.replace(/^\/review\//, "") ?? undefined };
    },
  }), [load, mapped, mutate, projectId]);

  return <ProjectRoom room={mapped?.room} permissions={mapped?.permissions ?? NO_PERMISSIONS} state={state} adapter={adapter} missionAssignments={missionAssignments} actionError={actionError} onOpenChat={onOpenChat} onRetryLoad={() => void load("manual")} />;
}
