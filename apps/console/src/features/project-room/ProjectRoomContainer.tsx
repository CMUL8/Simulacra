import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  addCmul8Comment,
  approveCmul8Graph,
  createCmul8Room,
  getCmul8Room,
  reviewCmul8Task,
  transitionCmul8Task,
  type Cmul8RoomPayload,
} from "../../api";
import type { GraphComment } from "../operation-graph";
import type { ReviewDecision } from "../shared";
import type { DurableTaskState, ProjectRoomFeatureAdapter, ProjectRoomPermissions } from "./contracts";
import { mapCmul8RoomPayload } from "./mapper";
import { ProjectRoom } from "./ProjectRoom";

const NO_PERMISSIONS: ProjectRoomPermissions = { manageTasks: false, reviewTasks: false, reviewGraph: false, handoff: false, invite: false, comment: false };

function commandDecision(decision: ReviewDecision): string {
  if (decision === "approved") return "approve";
  if (decision === "changes_requested") return "request_changes";
  return "reject";
}

function message(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Project Room request failed";
}

function mentions(body: string): Array<{ ref_type: string; ref_id: string }> {
  return [...new Set([...body.matchAll(/(?:^|\s)@([A-Za-z0-9][A-Za-z0-9_.-]{0,127})/g)].map((match) => match[1]!))].map((ref_id) => ({ ref_type: "actor", ref_id }));
}

export function ProjectRoomContainer({ projectId }: { projectId: string }) {
  const [payload, setPayload] = useState<Cmul8RoomPayload | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "forbidden">("loading");
  const [actionError, setActionError] = useState<string>();

  const load = useCallback(async () => {
    if (!payload) setState("loading");
    setActionError(undefined);
    try {
      let next: Cmul8RoomPayload;
      try {
        next = await getCmul8Room(projectId);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
        next = await createCmul8Room(projectId);
      }
      setPayload(next);
      setState("ready");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) setState("forbidden");
      else setState("error");
      setActionError(message(error));
    }
  }, [projectId, payload]);

  useEffect(() => { setPayload(null); setState("loading"); void load(); }, [projectId]); // load is intentionally reset by project identity only

  const mapped = useMemo(() => payload ? mapCmul8RoomPayload(payload) : null, [payload]);
  const mutate = useCallback(async (operation: () => Promise<unknown>) => {
    setActionError(undefined);
    try { await operation(); await load(); } catch (error) { setActionError(message(error)); }
  }, [load]);

  const adapter = useMemo<Partial<ProjectRoomFeatureAdapter>>(() => ({
    transitionTask: async (taskId, next, revision) => { await mutate(() => transitionCmul8Task(projectId, taskId, next, revision)); },
    submitTaskReview: async (taskId, decision, note, revision) => { await mutate(() => reviewCmul8Task(projectId, taskId, commandDecision(decision), note ?? "", revision)); },
    reconnect: load,
    approveGraph: async (revisionHash) => { await mutate(() => approveCmul8Graph(projectId, revisionHash)); },
    addComment: async (revisionId, body, section): Promise<GraphComment> => {
      const created = await addCmul8Comment(projectId, { body, target_type: "graph_element", target_id: revisionId, graph_revision: revisionId, graph_path: section?.startsWith("/") ? section : `/review/${section ?? "general"}`, mentions: mentions(body) });
      await load();
      return { id: created.id, author: created.author_id, body: created.body, createdAt: created.created_at, resolved: false, mentions: created.mentions?.map((item) => `${item.ref_type}:${item.ref_id}`), section: created.graph_path?.replace(/^\/review\//, "") ?? undefined };
    },
  }), [load, mutate, projectId]);

  return <ProjectRoom room={mapped?.room} permissions={mapped?.permissions ?? NO_PERMISSIONS} state={state} adapter={adapter} actionError={actionError} onRetryLoad={() => void load()} />;
}
