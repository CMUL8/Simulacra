import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import type { Snapshot } from "../api";
import { AgentShell } from "./AgentShell";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("first Mission readiness is visible in the live workspace container", async () => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(api, "getMission").mockResolvedValue({
    mission: { title: "August close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [],
    readiness: { graph: { status: "pending_approval", revision: 1 }, crew_count: 0 },
  });
  vi.spyOn(api, "getCmul8Room").mockRejectedValue(new Error("not ready"));
  vi.spyOn(api, "subscribeEvents").mockReturnValue(() => undefined);
  const snapshot = {
    project: {
      id: "mission_close", prompt: "Close August", goal: "Verified close", phase: "ready", plan_approved: false,
      status: "ready", gates_status: "pending", deployed: false, deploy_url: null, chat: [],
      app_config: { title: "August close" }, row_count: 0, checkpoints: [], active_checkpoint: 0,
      plan_preview: { row_count: 0, high_risk: 0, vendors: [], files: [], summary: "", sample_rows: [] },
    },
    preview_data: { columns: [], rows: [], row_count: 0 }, preview_url: null,
  } as unknown as Snapshot;

  render(<AgentShell
    variant="workspace" snapshot={snapshot} files={[]} input="" busy={false} error={null} traces={[]} sidebarOpen
    onToggleSidebar={vi.fn()} onInput={vi.fn()} onSend={vi.fn()} onOpenPreview={vi.fn()} onGovernance={vi.fn()} onDismissError={vi.fn()}
  />);

  expect(await screen.findByRole("heading", { name: "Ready your Mission" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add a source" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Shape the crew" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve how the crew will work" })).toBeInTheDocument();
  await waitFor(() => expect(api.getMission).toHaveBeenCalledWith("mission_close"));
});
