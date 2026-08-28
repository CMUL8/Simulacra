import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { WorkActionTarget, WorkItem } from "../../../api";
import { WorkList } from "./WorkList";

const response = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
}));

const item = (overrides: Partial<WorkItem> = {}): WorkItem => ({
  source_type: "assignment",
  source_id: "private_assignment_7",
  mission_id: "mission_close",
  revision: 2,
  title: "Reconcile unmatched invoices",
  summary: "Three records need a human decision.",
  state: "needs_you",
  assignee: { id: "human_ada", display_name: "Ada", kind: "human", avatar_url: null },
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-28T09:00:00Z",
  allowed_actions: ["open", "review_work"],
  action_targets: {
    open: { kind: "task", id: "task_target_7", revision: 2 },
    review_work: { kind: "task", id: "task_target_7", revision: 2 },
  },
  ...overrides,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

test("work_deep_link_opens_exact_item_and_primary_action_reaches_review_surface", async () => {
  const work = item({ allowed_actions: ["open", "verify_output"], action_targets: { verify_output: { kind: "output", id: "output_exact", file_id: "file_exact", revision: 2 } } });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [{ id: "mission_close", title: "August close" }], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList />);
  fireEvent.click(await screen.findByRole("button", { name: "Review evidence" }));
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close/files?output=file_exact&action=verify_output");

  cleanup();
  render(<WorkList missionId="mission_close" focusItemId="private_assignment_7" focusAction="verify_output" />);
  const detail = await screen.findByRole("dialog", { name: "Work details" });
  expect(detail).toHaveTextContent("Reconcile unmatched invoices");
  expect(within(detail).getByRole("button", { name: "Open output and evidence" })).toHaveFocus();
  fireEvent.click(within(detail).getByRole("button", { name: "Open output and evidence" }));
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close/files?output=file_exact&action=verify_output");
});

test("update_work_offers_only_exact_server_returned_next_states", async () => {
  const work = item({
    state: "in_progress",
    allowed_actions: ["update_work"],
    action_targets: { update_work: { kind: "task", id: "task_exact", revision: 7, next_states: ["in_review"] } },
  });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList missionId="mission_close" focusItemId={work.source_id} focusAction="update_work" />);
  const select = await screen.findByRole("combobox", { name: "Next state" });
  expect(within(select).getAllByRole("option").map((option) => option.getAttribute("value"))).toEqual(["in_review"]);
  expect(screen.queryByRole("option", { name: "Start work" })).not.toBeInTheDocument();
});

test("work_prioritizes_review_or_decision_over_generic_update", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [item({ allowed_actions: ["open", "update_work", "verify_output", "decide_checkpoint"] })], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList />);
  expect(await screen.findByRole("button", { name: "Review decision" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Update" })).not.toBeInTheDocument();
});

test("global_work_access_loss_clears_state_and_exits_loading", async () => {
  let preferenceWriteDenied = false;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/workspace/preferences/work-view")) {
      preferenceWriteDenied = true;
      return response({ detail: { message: "private state" } }, 403);
    }
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [item()], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Unexpected request ${path} ${init?.method || "GET"}`);
  });
  render(<WorkList />);
  expect(await screen.findByText("Reconcile unmatched invoices")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Board" }));
  await waitFor(() => expect(preferenceWriteDenied).toBe(true));
  expect(await screen.findByRole("alert")).toHaveTextContent("access");
  expect(screen.queryByText("Reconcile unmatched invoices")).not.toBeInTheDocument();
  expect(screen.queryByText("Loading Missions Work…")).not.toBeInTheDocument();
});

test("global_work_initial_access_loss_exits_loading_without_requesting_protected_rows", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/workspace/preferences")) return response({ detail: { message: "private state" } }, 403);
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Protected Work should not be requested after access loss: ${path}`);
  });
  render(<WorkList />);
  expect(await screen.findByRole("alert")).toHaveTextContent("access");
  expect(screen.queryByText("Loading Missions Work…")).not.toBeInTheDocument();
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/workspace/work"))).toBe(false);
});

test("work_drawer_escape_traps_focus_and_restores_the_opener", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [item({ allowed_actions: ["review_work"] })], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList />);
  const opener = await screen.findByRole("button", { name: "Open Reconcile unmatched invoices" });
  fireEvent.click(opener);
  const dialog = screen.getByRole("dialog", { name: "Work details" });
  const close = within(dialog).getByRole("button", { name: "Close Work details" });
  await waitFor(() => expect(close).toHaveFocus());
  fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
  expect(within(dialog).getByRole("button", { name: "Approve work" })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(opener).toHaveFocus());
});

test.each([
  {
    action: "claim_work",
    label: "Take ownership",
    target: { kind: "task", id: "task_claim_exact", revision: 7 },
    path: "/projects/mission_close/cmul8/tasks/task_claim_exact/claim?expected_revision=7",
    act: () => fireEvent.click(within(screen.getByRole("dialog", { name: "Work details" })).getByRole("button", { name: "Take ownership" })),
  },
  {
    action: "update_work",
    label: "Update",
    target: { kind: "task", id: "task_update_exact", revision: 8, next_states: ["in_review"] },
    path: "/projects/mission_close/cmul8/tasks/task_update_exact/transition",
    act: () => {
      fireEvent.change(screen.getByRole("combobox", { name: "Next state" }), { target: { value: "in_review" } });
      fireEvent.click(screen.getByRole("button", { name: "Send for review" }));
    },
  },
  {
    action: "review_work",
    label: "Review",
    target: { kind: "task", id: "task_review_exact", revision: 9 },
    path: "/projects/mission_close/cmul8/tasks/task_review_exact/reviews",
    act: () => {
      fireEvent.change(screen.getByRole("textbox", { name: "Review note" }), { target: { value: "Evidence checked." } });
      fireEvent.click(screen.getByRole("button", { name: "Approve work" }));
    },
  },
  {
    action: "decide_checkpoint",
    label: "Review decision",
    target: { kind: "approval", id: "approval_exact", revision: 10, run_revision: 4 },
    path: "/projects/mission_close/mission/approvals/approval_exact",
    act: () => fireEvent.click(screen.getByRole("button", { name: "Approve and continue" })),
  },
  {
    action: "retry_work",
    label: "Review restart",
    target: { kind: "run", id: "run_exact", revision: 12 },
    path: "/projects/mission_close/mission/runs/run_exact/retry",
    act: () => fireEvent.click(screen.getByRole("button", { name: "Retry work" })),
  },
])("$action uses only its server-returned action target and reports success", async ({ action, label, target, path, act }) => {
  const work = item({
    source_id: `row_${action}`,
    state: action === "update_work" ? "in_progress" : "needs_you",
    allowed_actions: [action],
    action_targets: { [action]: target as WorkActionTarget },
  });
  const mutation = vi.fn();
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const requestPath = String(input);
    if (requestPath.includes(path)) {
      mutation(requestPath, init);
      return action === "claim_work" ? response({ id: target.id, revision: target.revision + 1 }) : response({});
    }
    if (requestPath.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (requestPath.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    throw new Error(`Unexpected request ${requestPath}`);
  });
  window.history.replaceState({}, "", `/missions/mission_close/work?item=${work.source_id}&action=${action}`);
  render(<WorkList missionId="mission_close" focusItemId={work.source_id} focusAction={action} />);
  expect(await screen.findByRole("dialog", { name: "Work details" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Reconcile unmatched invoices" })).toBeInTheDocument();
  expect(within(screen.getByRole("dialog", { name: "Work details" })).getByRole("region", { name: label })).toBeInTheDocument();
  act();
  await waitFor(() => expect(mutation).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole("status")).toHaveTextContent(/saved|approved|verified|restarted|claimed|updated/i);
  expect(mutation.mock.calls[0][0]).not.toContain(work.source_id);
});

test.each([
  ["Approve work", "approve"],
  ["Request changes", "request_changes"],
  ["Reject work", "reject"],
])("review_work sends the exact %s decision", async (buttonName, decision) => {
  const work = item({
    allowed_actions: ["review_work"],
    action_targets: { review_work: { kind: "task", id: "task_review_exact", revision: 9 } },
  });
  const bodies: Array<Record<string, unknown>> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/reviews")) {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return response({});
    }
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList missionId="mission_close" focusItemId={work.source_id} focusAction="review_work" />);
  const dialog = await screen.findByRole("dialog", { name: "Work details" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Review note" }), { target: { value: "Evidence checked." } });
  fireEvent.click(within(dialog).getByRole("button", { name: buttonName }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toEqual({ decision, note: "Evidence checked.", expected_revision: 9 });
});

test("work_verify_output_routes_to_the_server_file_without_mutating", async () => {
  const work = item({
    source_id: "row_verify",
    allowed_actions: ["verify_output"],
    action_targets: { verify_output: { kind: "output", id: "deliverable_exact", file_id: "file_exact", revision: 11 } },
  });
  const mutation = vi.fn();
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (init?.method && init.method !== "GET") mutation(path, init);
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList missionId="mission_close" focusItemId={work.source_id} focusAction="verify_output" />);
  fireEvent.click(await screen.findByRole("button", { name: "Open output and evidence" }));
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close/files?output=file_exact&action=verify_output");
  expect(mutation).not.toHaveBeenCalled();
});

test.each([
  ["run", "run_deep", "retry_work", "run"],
  ["approval", "approval_deep", "decide_checkpoint", "approval"],
  ["output", "output_deep", "verify_output", "output"],
])("deep link %s resolves only through a returned Work item", async (queryKey, targetId, action, kind) => {
  const work = item({
    source_id: `row_for_${queryKey}`,
    allowed_actions: [action],
    action_targets: { [action]: { kind: kind as WorkActionTarget["kind"], id: targetId, revision: 3, ...(kind === "approval" ? { run_revision: 2 } : {}) } },
  });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  window.history.replaceState({}, "", `/missions/mission_close/work?${queryKey}=${targetId}&action=${action}`);
  render(<WorkList missionId="mission_close" />);
  expect(await screen.findByRole("dialog", { name: "Work details" })).toHaveTextContent("Reconcile unmatched invoices");
  expect(document.body.textContent).not.toContain(targetId);
});

test("review_plan_opens_the_plan_recovery_surface", async () => {
  const work = item({
    source_id: "row_plan_recovery",
    allowed_actions: ["review_plan"],
    action_targets: { review_plan: { kind: "plan", id: "plan_screened", revision: 4 } },
  });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [work], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList missionId="mission_close" focusItemId={work.source_id} focusAction="review_plan" />);
  fireEvent.click(await screen.findByRole("button", { name: "Review plan" }));
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close/conversation?focus=plan-approval");
  expect(document.body.textContent).not.toContain("plan_screened");
});

test("a_stale_work_action_refreshes_the_exact_item_while_access_loss_clears_it", async () => {
  let workReads = 0;
  let mutationStatus = 409;
  const work = item({ allowed_actions: ["claim_work"], action_targets: { claim_work: { kind: "task", id: "task_exact", revision: 3 } } });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/claim?")) return response({ detail: { message: "private" } }, mutationStatus);
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) {
      workReads += 1;
      return response({ items: [work], next_cursor: null });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList missionId="mission_close" focusItemId={work.source_id} focusAction="claim_work" />);
  const dialog = await screen.findByRole("dialog", { name: "Work details" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Take ownership" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("refreshed");
  expect(workReads).toBeGreaterThan(1);

  mutationStatus = 403;
  fireEvent.click(within(dialog).getByRole("button", { name: "Take ownership" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByText("Reconcile unmatched invoices")).not.toBeInTheDocument();
});

test("work_list_does_not_offer_disallowed_action", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [item({ allowed_actions: ["open"] })], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [{ id: "mission_close", title: "August close" }], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList />);
  expect(await screen.findByText("Reconcile unmatched invoices")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open details" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
  expect(screen.queryByText("private_assignment_7")).not.toBeInTheDocument();
});

test("work_list_uses_the_first_server_permitted_consequential_action", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [item({ allowed_actions: ["open", "verify_output", "not_a_public_action"] })], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList />);
  expect(await screen.findByRole("button", { name: "Review evidence" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Open details" })).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("not_a_public_action");
});

test("work_list_restores_saved_view_and_filters", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/workspace/preferences/work-view")) return response({
      work_view_preference: { scope: "workspace", view: "list", filters: {}, revision: 5, updated_at: "2026-08-28T10:00:00Z" },
    });
    if (path.endsWith("/workspace/preferences")) return response({
      work_view_preferences: [{ scope: "workspace", view: "board", filters: { bucket: "ready_for_review" }, revision: 4, updated_at: "2026-08-28T09:00:00Z" }],
      notification_preference: { revision: 0 },
    });
    if (path.includes("/workspace/work")) {
      expect(path).toContain("bucket=ready_for_review");
      return response({ items: [item({ state: "ready_for_review", title: "Review the close pack" })], next_cursor: null });
    }
    if (path.includes("/missions?")) return response({ items: [{ id: "mission_close", title: "August close" }], next_cursor: null });
    throw new Error(`Unexpected request ${path} ${init?.method || "GET"}`);
  });
  render(<WorkList />);
  expect(await screen.findByRole("button", { name: "Board" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getAllByText("Ready for review").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) => String(input).includes("work-view") && init?.method === "PUT")).toBe(true));
});

test("a_saved_view_conflict_reloads_the_server_value", async () => {
  let preferenceReads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/preferences/work-view")) return response({ detail: { code: "revision_conflict", message: "changed" } }, 409);
    if (path.endsWith("/workspace/preferences")) {
      preferenceReads += 1;
      return response({
        work_view_preferences: preferenceReads === 1 ? [] : [{ scope: "workspace", view: "board", filters: {}, revision: 2, updated_at: null }],
        notification_preference: { revision: 0 },
      });
    }
    if (path.includes("/workspace/work")) return response({ items: [], next_cursor: null });
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkList />);
  await screen.findByText("No Work matches this view.");
  fireEvent.click(screen.getByRole("button", { name: "Board" }));
  expect(await screen.findByText(/saved view changed elsewhere/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Board" })).toHaveAttribute("aria-pressed", "true");
});
