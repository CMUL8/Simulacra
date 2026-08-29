import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError } from "../../../api";
import type { MissionDraft, MissionDraftRepository } from "./missionDraftStore";
import { useMissionBootstrap } from "./useMissionBootstrap";

function repository(seed: MissionDraft | null = null): MissionDraftRepository & { current: MissionDraft | null } {
  return {
    current: seed,
    async load() { return this.current; },
    async save(draft) { this.current = structuredClone(draft); },
    async discard() { this.current = null; },
  };
}

const complete = (projectId: string) => ({
  status: "COMPLETE" as const,
  transaction_id: "tx_fixed",
  project: { id: projectId },
  provisioning: false as const,
});

afterEach(() => vi.restoreAllMocks());

test("failed_source_retry_does_not_omit_the_source_or_create_early", async () => {
  const store = repository();
  const stage = vi.fn()
    .mockRejectedValueOnce(new Error("private path leaked"))
    .mockResolvedValueOnce({ source_ref: "source_1", sha256: "abc", filename: "source.csv", media_type: "text/csv" });
  const create = vi.fn().mockResolvedValue(complete("mission_1"));
  const navigate = vi.fn();
  const { result } = renderHook(() => useMissionBootstrap({
    workspaceId: "workspace_a", humanId: "human_a", repository: store,
    clients: { stageSource: stage, createMission: create, getBootstrap: vi.fn() }, onComplete: navigate,
  }));
  await waitFor(() => expect(result.current.ready).toBe(true));
  const source = new File(["a,b"], "source.csv", { type: "text/csv" });
  await act(async () => { await result.current.setFiles([source]); });
  await act(async () => { await result.current.create("Prepare a verified report"); });
  expect(create).not.toHaveBeenCalled();
  expect(result.current.error).toBe("One source could not be added. Try that source again before creating the Mission.");

  await act(async () => { await result.current.create("Prepare a verified report"); });
  expect(stage).toHaveBeenCalledTimes(2);
  expect(create).toHaveBeenCalledTimes(1);
  expect(create.mock.calls[0]?.[0].staged_source_refs).toEqual(["source_1"]);
  expect(navigate).toHaveBeenCalledWith("mission_1");
});

test("lost_response_replays_the_exact_frozen_request", async () => {
  const store = repository();
  const create = vi.fn()
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockResolvedValueOnce(complete("mission_exact"));
  const { result } = renderHook(() => useMissionBootstrap({
    workspaceId: "workspace_a", humanId: "human_a", repository: store,
    clients: { stageSource: vi.fn(), createMission: create, getBootstrap: vi.fn() }, onComplete: vi.fn(),
  }));
  await waitFor(() => expect(result.current.ready).toBe(true));
  await act(async () => { await result.current.create("Create the board report"); });
  await act(async () => { await result.current.retry(); });
  expect(create).toHaveBeenCalledTimes(2);
  expect(create.mock.calls[1]?.[0]).toEqual(create.mock.calls[0]?.[0]);
});

test("reload_resumes_a_persisted_transaction_without_duplicate_creation", async () => {
  const saved: MissionDraft = {
    version: 1, workspaceId: "workspace_a", humanId: "human_a", outcome: "Close the month",
    bootstrapRequestId: "bootstrap_fixed", sources: [], frozenRequest: {
      client_request_id: "bootstrap_fixed", prompt: "Close the month", goal: "Close the month",
      design_brief: null, artifact_kind: "data_app", staged_source_refs: [],
    }, transactionId: "tx_fixed", projectId: "mission_fixed", updatedAt: 1,
  };
  const store = repository(saved);
  const create = vi.fn();
  const status = vi.fn().mockResolvedValue(complete("mission_fixed"));
  const navigate = vi.fn();
  const { result } = renderHook(() => useMissionBootstrap({
    workspaceId: "workspace_a", humanId: "human_a", repository: store,
    clients: { stageSource: vi.fn(), createMission: create, getBootstrap: status }, onComplete: navigate,
  }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("mission_fixed"));
  expect(result.current.ready).toBe(true);
  expect(status).toHaveBeenCalledWith("tx_fixed");
  expect(create).not.toHaveBeenCalled();
});

test("reload_replays_one_exact_frozen_request_after_a_lost_post_response", async () => {
  const frozenRequest = {
    client_request_id: "bootstrap_fixed", prompt: "Close the month", goal: "Close the month",
    design_brief: null, artifact_kind: "data_app" as const, staged_source_refs: ["source_fixed"],
  };
  const saved: MissionDraft = {
    version: 1, workspaceId: "workspace_a", humanId: "human_a", outcome: "Close the month",
    bootstrapRequestId: "bootstrap_fixed", sources: [], frozenRequest, updatedAt: 1,
  };
  const create = vi.fn().mockResolvedValue(complete("mission_fixed"));
  const navigate = vi.fn();
  renderHook(() => useMissionBootstrap({
    workspaceId: "workspace_a", humanId: "human_a", repository: repository(saved),
    clients: { stageSource: vi.fn(), createMission: create, getBootstrap: vi.fn() }, onComplete: navigate,
  }));

  await waitFor(() => expect(navigate).toHaveBeenCalledWith("mission_fixed"));
  expect(create).toHaveBeenCalledTimes(1);
  expect(create).toHaveBeenCalledWith(frozenRequest);
});

test("aborted_bootstrap_is_blocked_without_a_false_retry", async () => {
  const store = repository();
  const create = vi.fn().mockResolvedValue({ status: "ABORTED", code: "bootstrap_aborted" });
  const { result } = renderHook(() => useMissionBootstrap({
    workspaceId: "workspace_a", humanId: "human_a", repository: store,
    clients: { stageSource: vi.fn(), createMission: create, getBootstrap: vi.fn() }, onComplete: vi.fn(),
  }));
  await waitFor(() => expect(result.current.ready).toBe(true));
  await act(async () => { await result.current.create("Prepare a verified report"); });
  expect(result.current.blocked).toBe(true);
  expect(result.current.canRetry).toBe(false);
  expect(result.current.error).toContain("Start a new Mission");
});

test("unavailable_persisted_bootstrap_requires_a_new_mission_without_exposing_the_server_error", async () => {
  const saved: MissionDraft = {
    version: 1, workspaceId: "workspace_a", humanId: "human_a", outcome: "Close the month",
    bootstrapRequestId: "bootstrap_fixed", sources: [], frozenRequest: {
      client_request_id: "bootstrap_fixed", prompt: "Close the month", goal: "Close the month",
      design_brief: null, artifact_kind: "data_app", staged_source_refs: [],
    }, transactionId: "tx_missing", projectId: "mission_missing", updatedAt: 1,
  };
  const serverError = new ApiError(404, "/app/runs/private/tx_missing.json", "bootstrap_unavailable");
  const status = vi.fn().mockRejectedValue(serverError);
  const { result } = renderHook(() => useMissionBootstrap({
    workspaceId: "workspace_a", humanId: "human_a", repository: repository(saved),
    clients: { stageSource: vi.fn(), createMission: vi.fn(), getBootstrap: status }, onComplete: vi.fn(),
  }));

  await waitFor(() => expect(result.current.blocked).toBe(true));
  expect(result.current.canRetry).toBe(false);
  expect(result.current.error).toContain("Start a new Mission");
  expect(result.current.error).not.toContain("/app/");
});
