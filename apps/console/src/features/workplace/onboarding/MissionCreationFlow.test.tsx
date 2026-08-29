import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { MissionCreationFlow } from "./MissionCreationFlow";
import type { MissionDraft, MissionDraftRepository } from "./missionDraftStore";
import newMissionStyles from "./new-mission.css?raw";

const repository = (): MissionDraftRepository => {
  let value: MissionDraft | null = null;
  return { async load() { return value; }, async save(next) { value = next; }, async discard() { value = null; } };
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("back_and_remove_controls_keep_a_40px_touch_target", () => {
  const controls = newMissionStyles.match(/\.mission-create-back,\s*\.mission-source-list button\s*\{([^}]*)\}/)?.[1] || "";
  expect(controls).toMatch(/min-block-size:\s*2\.5rem/);
  expect(controls).toMatch(/min-inline-size:\s*2\.5rem/);
});

test("one_focused_outcome_and_optional_sources_create_the_mission", async () => {
  const create = vi.fn().mockResolvedValue({ status: "COMPLETE", transaction_id: "tx", project: { id: "mission_1" }, provisioning: false });
  const navigate = vi.fn();
  render(<MissionCreationFlow workspaceId="workspace_a" humanId="human_a" repository={repository()}
    clients={{ stageSource: vi.fn(), createMission: create, getBootstrap: vi.fn() }} onComplete={navigate} onCancel={vi.fn()} />);
  const outcome = await screen.findByRole("textbox", { name: "Mission outcome" });
  expect(screen.queryByText(/runtime|provider|model|Codex|MCP/i)).not.toBeInTheDocument();
  fireEvent.change(outcome, { target: { value: "Reconcile invoices and prepare a verified exception report" } });
  fireEvent.click(screen.getByRole("button", { name: "Create Mission" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("mission_1"));
  expect(create).toHaveBeenCalledTimes(1);
});

test("source_failure_has_safe_copy_and_never_looks_complete", async () => {
  const stage = vi.fn().mockRejectedValue(new Error("/app/runs/private/source.csv"));
  const create = vi.fn();
  render(<MissionCreationFlow workspaceId="workspace_a" humanId="human_a" repository={repository()}
    clients={{ stageSource: stage, createMission: create, getBootstrap: vi.fn() }} onComplete={vi.fn()} onCancel={vi.fn()} />);
  fireEvent.change(await screen.findByRole("textbox", { name: "Mission outcome" }), { target: { value: "Review this source" } });
  const input = screen.getByLabelText("Add source files");
  fireEvent.change(input, { target: { files: [new File(["x"], "source.csv", { type: "text/csv" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Create Mission" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("One source could not be added");
  expect(screen.getByRole("alert")).not.toHaveTextContent("/app/");
  expect(create).not.toHaveBeenCalled();
});

test("recoverable_failure_offers_retry_and_blocked_failure_starts_a_clean_mission", async () => {
  const saved = repository();
  const create = vi.fn()
    .mockRejectedValueOnce(new Error("temporary"))
    .mockResolvedValueOnce({ status: "ABORTED", code: "bootstrap_aborted" });
  render(<MissionCreationFlow workspaceId="workspace_a" humanId="human_a" repository={saved}
    clients={{ stageSource: vi.fn(), createMission: create, getBootstrap: vi.fn() }} onComplete={vi.fn()} onCancel={vi.fn()} />);

  fireEvent.change(await screen.findByRole("textbox", { name: "Mission outcome" }), { target: { value: "Prepare the verified close" } });
  fireEvent.click(screen.getByRole("button", { name: "Create Mission" }));
  expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: "Start a new Mission" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Start a new Mission" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Mission outcome" })).toHaveValue(""));
});

test("shows_sources_as_optional_and_allows_removing_them_before_creation", async () => {
  render(<MissionCreationFlow workspaceId="workspace_a" humanId="human_a" repository={repository()}
    clients={{ stageSource: vi.fn(), createMission: vi.fn(), getBootstrap: vi.fn() }} onComplete={vi.fn()} onCancel={vi.fn()} />);
  const input = await screen.findByLabelText("Add source files");
  fireEvent.change(input, { target: { files: [new File(["one"], "close.csv", { type: "text/csv" }), new File(["two"], "notes.pdf", { type: "application/pdf" })] } });
  expect(await screen.findByText("close.csv")).toBeInTheDocument();
  expect(screen.getByText("notes.pdf")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove close.csv" }));
  await waitFor(() => expect(screen.queryByText("close.csv")).not.toBeInTheDocument());
});
