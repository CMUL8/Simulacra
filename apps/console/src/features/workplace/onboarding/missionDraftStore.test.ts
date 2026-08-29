import { afterEach, expect, test } from "vitest";

import {
  addMissionDraftFiles,
  createMissionDraft,
  missionDraftKey,
  type MissionDraft,
} from "./missionDraftStore";

afterEach(() => {
  localStorage.clear();
});

test("stable_ids_and_blobs_survive_draft_updates", () => {
  const ids = ["bootstrap-fixed", "source-fixed"];
  const draft = createMissionDraft("workspace_a", "human_a", () => ids.shift()!);
  const file = new File(["invoice,total\nA,42"], "invoices.csv", { type: "text/csv", lastModified: 7 });
  const withFile = addMissionDraftFiles(draft, [file], () => ids.shift()!);
  const changed = addMissionDraftFiles({ ...withFile, outcome: "Reconcile invoices" }, [file], () => "unused");

  expect(changed.bootstrapRequestId).toBe("bootstrap-fixed");
  expect(changed.sources).toHaveLength(1);
  expect(changed.sources[0]).toMatchObject({ requestId: "source-fixed", name: "invoices.csv", mediaType: "text/csv" });
  expect(changed.sources[0]?.blob).toBe(file);
  expect(missionDraftKey(changed)).toBe("workspace_a::human_a");
});

test("draft_identity_is_bound_to_one_authenticated_human_and_workspace", () => {
  const draft: MissionDraft = createMissionDraft("workspace_a", "human_a", () => "fixed");
  expect(missionDraftKey(draft)).not.toBe(missionDraftKey({ ...draft, workspaceId: "workspace_b" }));
  expect(missionDraftKey(draft)).not.toBe(missionDraftKey({ ...draft, humanId: "human_b" }));
});
