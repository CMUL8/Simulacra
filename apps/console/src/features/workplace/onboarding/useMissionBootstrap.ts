import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  createWorkplaceMission,
  getWorkplaceMissionBootstrap,
  stageMissionSource,
  type MissionBootstrapRequest,
  type MissionBootstrapResult,
  type StagedMissionSource,
} from "../../../api";
import {
  addMissionDraftFiles,
  createMissionDraft,
  createMissionDraftRepository,
  removeMissionDraftSource,
  type MissionDraft,
  type MissionDraftRepository,
} from "./missionDraftStore";

export type MissionBootstrapClients = {
  stageSource(file: File, clientRequestId: string): Promise<StagedMissionSource>;
  createMission(body: MissionBootstrapRequest): Promise<MissionBootstrapResult>;
  getBootstrap(transactionId: string): Promise<MissionBootstrapResult>;
};

type MissionBootstrapOptions = {
  workspaceId: string;
  humanId: string;
  repository?: MissionDraftRepository;
  clients?: MissionBootstrapClients;
  onComplete(projectId: string): void;
};

const defaultClients: MissionBootstrapClients = {
  stageSource: stageMissionSource,
  createMission: createWorkplaceMission,
  getBootstrap: getWorkplaceMissionBootstrap,
};

function publicFailure(error: unknown): { message: string; blocked: boolean } {
  if (error instanceof ApiError && (
    error.code === "bootstrap_aborted"
    || error.code === "bootstrap_unavailable"
    || error.code === "idempotency_mismatch"
  )) {
    return { message: "This Mission could not be created safely. Start a new Mission to try again.", blocked: true };
  }
  return { message: "Missions could not finish creating this Mission. Try again; your outcome and sources are saved.", blocked: false };
}

function isComplete(result: MissionBootstrapResult): result is Extract<MissionBootstrapResult, { status: "COMPLETE" }> {
  return result.status === "COMPLETE";
}

function projectIdFrom(result: Extract<MissionBootstrapResult, { status: "COMPLETE" }>): string {
  return result.project.id || result.project_id || "";
}

export function useMissionBootstrap({ workspaceId, humanId, repository, clients = defaultClients, onComplete }: MissionBootstrapOptions) {
  const draftRepository = useMemo(
    () => repository || createMissionDraftRepository(workspaceId, humanId),
    [humanId, repository, workspaceId],
  );
  const [draft, setDraft] = useState<MissionDraft | null>(null);
  const [ready, setReady] = useState(false);
  const [working, setWorking] = useState(false);
  const [phase, setPhase] = useState<"editing" | "sources" | "provisioning" | "blocked">("editing");
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  const timer = useRef<number | null>(null);
  const active = useRef(true);
  const operation = useRef(false);
  const resumedRequestId = useRef<string | null>(null);
  const clientsRef = useRef(clients);
  clientsRef.current = clients;
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;

  const finish = useCallback(async (result: Extract<MissionBootstrapResult, { status: "COMPLETE" }>) => {
    const projectId = projectIdFrom(result);
    if (!projectId) {
      setError("Missions finished setup but could not open the new Mission. Return to Missions and try again.");
      setWorking(false);
      operation.current = false;
      return;
    }
    await draftRepository.discard();
    if (!active.current) return;
    setDraft(createMissionDraft(workspaceId, humanId));
    setWorking(false);
    operation.current = false;
    setError(null);
    completeRef.current(projectId);
  }, [draftRepository, humanId, workspaceId]);

  const persistPending = useCallback(async (current: MissionDraft, result: Exclude<MissionBootstrapResult, { status: "COMPLETE" | "ABORTED" }>) => {
    const next: MissionDraft = {
      ...current,
      transactionId: result.transaction_id,
      projectId: result.project_id,
      updatedAt: Date.now(),
    };
    await draftRepository.save(next);
    if (active.current) setDraft(next);
    return next;
  }, [draftRepository]);

  const markAborted = useCallback(() => {
    setBlocked(true);
    setWorking(false);
    operation.current = false;
    setPhase("blocked");
    setError("This Mission could not be created safely. Start a new Mission to try again.");
  }, []);

  const poll = useCallback(async (current: MissionDraft): Promise<void> => {
    if (!current.transactionId) return;
    operation.current = true;
    setWorking(true);
    setPhase("provisioning");
    try {
      const result = await clientsRef.current.getBootstrap(current.transactionId);
      if (!active.current) return;
      if (isComplete(result)) {
        await finish(result);
        return;
      }
      if (result.status === "ABORTED") {
        markAborted();
        return;
      }
      const saved = await persistPending(current, result);
      const wait = Math.max(1, Math.min(10, result.retry_after_seconds || 2)) * 1000;
      timer.current = window.setTimeout(() => void poll(saved), wait);
    } catch (caught) {
      if (!active.current) return;
      const failure = publicFailure(caught);
      if (failure.blocked) markAborted();
      else {
        setWorking(false);
        operation.current = false;
        setError(failure.message);
      }
    }
  }, [finish, markAborted, persistPending]);

  const save = useCallback(async (next: MissionDraft) => {
    setDraft(next);
    await draftRepository.save(next);
  }, [draftRepository]);

  const setOutcome = useCallback(async (outcome: string) => {
    if (!draft || draft.frozenRequest) return;
    await save({ ...draft, outcome, updatedAt: Date.now() });
  }, [draft, save]);

  const setFiles = useCallback(async (files: File[]) => {
    if (!draft || draft.frozenRequest) return;
    await save(addMissionDraftFiles(draft, files));
  }, [draft, save]);

  const removeFile = useCallback(async (sourceId: string) => {
    if (!draft || draft.frozenRequest) return;
    await save(removeMissionDraftSource(draft, sourceId));
  }, [draft, save]);

  const submitFrozen = useCallback(async (current: MissionDraft) => {
    if (!current.frozenRequest) return;
    operation.current = true;
    setWorking(true);
    setPhase("provisioning");
    setError(null);
    try {
      const result = await clientsRef.current.createMission(current.frozenRequest);
      if (!active.current) return;
      if (isComplete(result)) {
        await finish(result);
        return;
      }
      if (result.status === "ABORTED") {
        markAborted();
        return;
      }
      const saved = await persistPending(current, result);
      await poll(saved);
    } catch (caught) {
      if (!active.current) return;
      const failure = publicFailure(caught);
      if (failure.blocked) markAborted();
      else {
        setWorking(false);
        operation.current = false;
        setError(failure.message);
      }
    }
  }, [finish, markAborted, persistPending, poll]);

  useEffect(() => {
    active.current = true;
    void draftRepository.load()
      .then((saved) => {
        if (!active.current) return;
        const next = saved || createMissionDraft(workspaceId, humanId);
        setDraft(next);
        setReady(true);
        if (next.transactionId) {
          void poll(next);
        } else if (next.frozenRequest && resumedRequestId.current !== next.bootstrapRequestId) {
          resumedRequestId.current = next.bootstrapRequestId;
          void submitFrozen(next);
        }
      })
      .catch(() => {
        if (!active.current) return;
        setDraft(createMissionDraft(workspaceId, humanId));
        setReady(true);
        setBlocked(true);
        setPhase("blocked");
        setError("This browser cannot safely save a Mission draft. Enable site storage, then reload.");
      });
    return () => {
      active.current = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [draftRepository, humanId, poll, submitFrozen, workspaceId]);

  const create = useCallback(async (outcome: string) => {
    if (!draft || working || blocked || operation.current) return;
    const trimmed = outcome.trim();
    if (trimmed.length < 3) {
      setError("Describe the outcome this Mission should achieve.");
      return;
    }
    let current = { ...draft, outcome: trimmed, updatedAt: Date.now() };
    operation.current = true;
    setWorking(true);
    setPhase("sources");
    setError(null);
    await save(current);
    try {
      for (let index = 0; index < current.sources.length; index += 1) {
        const source = current.sources[index]!;
        if (source.staged) continue;
        if (!source.blob) throw new Error("source_blob_unavailable");
        const file = new File([source.blob], source.name, { type: source.mediaType, lastModified: source.lastModified });
        const staged = await clientsRef.current.stageSource(file, source.requestId);
        current = {
          ...current,
          sources: current.sources.map((item) => item.id === source.id ? { ...item, staged } : item),
          updatedAt: Date.now(),
        };
        await save(current);
      }
    } catch {
      if (!active.current) return;
      setWorking(false);
      operation.current = false;
      setPhase("editing");
      setError("One source could not be added. Try that source again before creating the Mission.");
      return;
    }
    const frozenRequest: MissionBootstrapRequest = current.frozenRequest || {
      client_request_id: current.bootstrapRequestId,
      prompt: trimmed,
      goal: trimmed,
      design_brief: null,
      artifact_kind: "data_app",
      staged_source_refs: current.sources.map((source) => source.staged!.source_ref),
    };
    current = { ...current, frozenRequest, updatedAt: Date.now() };
    await save(current);
    await submitFrozen(current);
  }, [blocked, draft, save, submitFrozen, working]);

  const retry = useCallback(async () => {
    if (!draft || blocked || working || operation.current) return;
    if (draft.transactionId) await poll(draft);
    else if (draft.frozenRequest) await submitFrozen(draft);
    else await create(draft.outcome);
  }, [blocked, create, draft, poll, submitFrozen, working]);

  const discard = useCallback(async () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    await draftRepository.discard();
    const next = createMissionDraft(workspaceId, humanId);
    setDraft(next);
    setBlocked(false);
    setWorking(false);
    operation.current = false;
    setPhase("editing");
    setError(null);
  }, [draftRepository, humanId, workspaceId]);

  return {
    ready,
    draft,
    working,
    busy: working,
    phase,
    error,
    blocked,
    canRetry: Boolean(error && !blocked && (draft?.frozenRequest || draft?.outcome)),
    setOutcome,
    setFiles,
    removeFile,
    create,
    retry,
    discard,
    startNew: discard,
  };
}
