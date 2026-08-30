import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { AttentionItem, MissionSummary, WorkspaceEventStream, WorkspaceEventStreamOptions } from "../../../api";
import { WorkplaceShell } from "./WorkplaceShell";
import attentionStyles from "../attention/attention.css?raw";
import conversationStyles from "../conversation/conversation.css?raw";
import workplaceStyles from "./workplace.css?raw";
import legacyStyles from "../../../styles.css?raw";

const bootstrapHarness = vi.hoisted(() => ({
  onComplete: null as null | ((missionId: string) => void),
}));

vi.mock("../onboarding/useMissionBootstrap", () => ({
  useMissionBootstrap: ({ onComplete }: { onComplete: (missionId: string) => void }) => {
    bootstrapHarness.onComplete = onComplete;
    return {
      ready: true,
      draft: { outcome: "", sources: [] },
      working: false,
      phase: "editing",
      error: null,
      blocked: false,
      canRetry: false,
      setOutcome: vi.fn(),
      setFiles: vi.fn(),
      removeFile: vi.fn(),
      create: vi.fn(),
      retry: vi.fn(),
      discard: vi.fn(),
    };
  },
}));

const response = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
}));

const mission = (id: string, overrides: Partial<MissionSummary> = {}): MissionSummary => ({
  id,
  title: `Mission ${id}`,
  outcome_summary: `Verified outcome for ${id}`,
  public_state: "running",
  updated_at: "2026-01-02T10:00:00Z",
  human_count: 2,
  agent_count: 3,
  active_work_count: 1,
  needs_human_count: 0,
  verified_output_count: 0,
  current_human_permissions: ["view_mission"],
  ...overrides,
});

const attentionItem = (id: string, overrides: Partial<AttentionItem> = {}): AttentionItem => ({
  id,
  mission_id: "mission_1",
  type: "assignment",
  title: `Attention ${id}`,
  summary: `Review ${id}`,
  source_event_id: `event_${id}`,
  subject_id: `task_${id}`,
  priority: 30,
  actionable: true,
  read: false,
  revision: 0,
  created_at: "2026-01-02T10:00:00Z",
  updated_at: "2026-01-02T10:00:00Z",
  deep_link: `/missions/mission_1?tab=work&item=task_${id}`,
  allowed_actions: ["open", "update_work"],
  ...overrides,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  bootstrapHarness.onComplete = null;
  window.history.replaceState({}, "", "/");
});

test("new_mission_route_is_resolved_before_mission_id_and_completion_replaces_into_conversation", async () => {
  window.history.replaceState({ old: true }, "", "/missions/new");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null }));
  render(<WorkplaceShell attentionEnabled bootstrapEnabled workspaceId="workspace_a" currentHumanId="human_a" onSearch={() => undefined} onSettings={() => undefined} />);

  expect(screen.getByRole("heading", { name: "Create a Mission" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Mission selected" })).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
  bootstrapHarness.onComplete?.("mission_ready");
  await waitFor(() => expect(window.location.pathname).toBe("/missions/mission_ready/conversation"));
});

test("new_mission_cta_is_flagged_and_flag_off_new_route_returns_to_the_list", async () => {
  window.history.replaceState({}, "", "/missions?state=active");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null }));
  const enabled = render(<WorkplaceShell attentionEnabled bootstrapEnabled workspaceId="workspace_a" currentHumanId="human_a" onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "New Mission" }));
  expect(window.location.pathname).toBe("/missions/new");
  enabled.unmount();

  window.history.replaceState({}, "", "/missions/new");
  render(<WorkplaceShell attentionEnabled bootstrapEnabled={false} workspaceId="workspace_a" currentHumanId="human_a" onSearch={() => undefined} onSettings={() => undefined} />);
  await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/missions?state=active"));
  expect(screen.queryByRole("button", { name: "New Mission" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Mission selected" })).not.toBeInTheDocument();
});

test("opens_needs_you_from_url_and_retains_filter_on_reload", async () => {
  window.history.replaceState({}, "", "/needs-you?filter=all");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/workspace/attention")) return response({ items: [], next_cursor: null, unread_count: 2, actionable_count: 1 });
    return response({ items: [], next_cursor: null });
  });
  const view = render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Needs you" })).toBeInTheDocument());
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("filter=all"), expect.any(Object));
  view.unmount();
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true"));
});

test("normalizes_invalid_workplace_query_values", async () => {
  window.history.replaceState({}, "", "/missions?state=private");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null }));
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await waitFor(() => expect(window.location.search).toBe("?state=active"));
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  expect(window.location.search).toBe("?state=all");
});

test("Mission cards open the canonical Conversation route", async () => {
  window.history.replaceState({}, "", "/missions?state=active");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [mission("close")], next_cursor: null }));
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Mission close/ }));
  expect(window.location.pathname).toBe("/missions/close/conversation");
});

test("mission_conversation_work_files_are_url_backed_and_survive_reload_and_back", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation?")) return response({ items: [], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "August close", objective: "Close with verified evidence" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "August close", objective: "Close with verified evidence" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [], next_cursor: null });
    if (path.includes("/projects/mission_close/files")) return response({ items: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  const first = render(<WorkplaceShell attentionEnabled conversationEnabled filesEnabled previewEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  const missionViews = await screen.findByRole("navigation", { name: "Mission views" });
  expect(within(missionViews).getByRole("button", { name: "Conversation" })).toHaveAttribute("aria-current", "page");
  const work = within(missionViews).getByRole("button", { name: "Work" });
  work.focus();
  expect(work).toHaveFocus();
  fireEvent.click(work);
  expect(window.location.pathname).toBe("/missions/mission_close/work");
  expect(await screen.findByRole("region", { name: "Mission Work" })).toBeInTheDocument();
  fireEvent.click(within(missionViews).getByRole("button", { name: "Files" }));
  expect(window.location.pathname).toBe("/missions/mission_close/files");
  expect(await screen.findByRole("region", { name: "Mission Files" })).toBeInTheDocument();
  first.unmount();

  render(<WorkplaceShell attentionEnabled conversationEnabled filesEnabled previewEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  const reloadedViews = await screen.findByRole("navigation", { name: "Mission views" });
  expect(within(reloadedViews).getByRole("button", { name: "Files" })).toHaveAttribute("aria-current", "page");
  window.history.back();
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(window.location.pathname).toBe("/missions/mission_close/work"));
  expect(within(screen.getByRole("navigation", { name: "Mission views" })).getByRole("button", { name: "Work" })).toHaveAttribute("aria-current", "page");
});

test("files_flag_off_hides_files_tab_and_normalizes_direct_files_url", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/files?preview=private_file");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation?")) return response({ items: [], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "August close", objective: "Close with verified evidence" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "August close", objective: "Close with verified evidence" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled filesEnabled={false} previewEnabled={false} onSearch={() => undefined} onSettings={() => undefined} />);
  await waitFor(() => expect(window.location.pathname).toBe("/missions/mission_close/conversation"));
  const views = await screen.findByRole("navigation", { name: "Mission views" });
  expect(within(views).queryByRole("button", { name: "Files" })).not.toBeInTheDocument();
  expect(window.location.search).toBe("");
});

test("mission_work_deep_link_opens_the_exact_review_and_close_clears_the_url_target", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/work?item=task_review&action=decide_checkpoint");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/workspace/preferences")) return response({ work_view_preferences: [], notification_preference: { revision: 0 } });
    if (path.includes("/workspace/work")) return response({ items: [{
      source_type: "approval", source_id: "task_review", mission_id: "mission_close", revision: 2,
      title: "Approve the exception", summary: "A human decision is needed before work continues.", state: "needs_you",
      assignee: null, created_at: "2026-08-27T09:00:00Z", updated_at: "2026-08-28T09:00:00Z",
      allowed_actions: ["open", "decide_checkpoint"],
      action_targets: { decide_checkpoint: { kind: "approval", id: "approval_exact", revision: 2, run_revision: 1 } },
    }], next_cursor: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "August close", objective: "Close with verified evidence" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "August close", objective: "Close with verified evidence" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled filesEnabled previewEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  const detail = await screen.findByRole("dialog", { name: "Work details" });
  expect(detail).toHaveTextContent("Approve the exception");
  await waitFor(() => expect(within(detail).getByRole("button", { name: "Approve and continue" })).toHaveFocus());
  fireEvent.click(within(detail).getByRole("button", { name: "Close Work details" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Work details" })).not.toBeInTheDocument());
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close/work");
});

test("Mission card origin state survives detail reload and Back to Missions", async () => {
  window.history.replaceState({}, "", "/missions?state=all");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/missions?")) return response({ items: [mission("close")], next_cursor: null });
    if (path.includes("/conversation")) return response({ items: [], next_before: null });
    if (path.endsWith("/projects/close/mission")) return response({
      mission: { title: "Close the month", objective: "Prepare the verified close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [],
      readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 },
    });
    if (path.endsWith("/projects/close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [] }, project: { id: "close", name: "Close the month", objective: "Prepare the verified close" },
      tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  const first = render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Mission close/ }));
  expect(window.location.pathname).toBe("/missions/close/conversation");
  expect(window.history.state.workplaceMissionState).toBe("all");
  await screen.findByRole("heading", { name: "Close the month" });
  first.unmount();

  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Back to Missions" }));
  expect(window.location.pathname + window.location.search).toBe("/missions?state=all");
});

test("uses_real_popstate_and_preserves_mission_and_attention_deep_links", async () => {
  window.history.replaceState({}, "", "/missions/mission_1?tab=work&item=task_1");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null }));
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  expect(screen.getByRole("heading", { name: "Mission selected" })).toBeInTheDocument();
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_1?tab=work&item=task_1");
  window.history.pushState({}, "", "/needs-you?filter=all");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Needs you" })).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  window.history.pushState({}, "", "/missions/mission_2?tab=conversation&attention=attention_2");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Mission selected" })).toBeInTheDocument());
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_2?tab=conversation&attention=attention_2");
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/projects"))).toBe(false);
});

test("settings_owns_account_route_and_restores_the_previous_workplace_location", async () => {
  window.history.replaceState({}, "", "/missions?state=all");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null }));
  const onSettings = vi.fn();
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={onSettings} />);
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(window.location.pathname).toBe("/settings/account");
  expect(window.history.state.workplaceReturnTo).toBe("/missions?state=all");
  await waitFor(() => expect(onSettings).toHaveBeenCalled());
  window.history.replaceState({}, "", "/settings/account");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument());
});

test("mission pagination dedupes rows and prevents concurrent load-more requests", async () => {
  window.history.replaceState({}, "", "/missions?state=active");
  let resolveSecond!: (value: Response) => void;
  const second = new Promise<Response>((resolve) => { resolveSecond = resolve; });
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("cursor=next")) return second;
    return response({ items: [mission("one")], next_cursor: "next" });
  });
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByRole("button", { name: /Mission one/ });
  const more = screen.getByRole("button", { name: "Load more Missions" });
  fireEvent.click(more);
  fireEvent.click(more);
  expect(fetcher.mock.calls.filter(([input]) => String(input).includes("cursor=next"))).toHaveLength(1);
  resolveSecond(await response({ items: [mission("one", { title: "Mission one updated" }), mission("two")], next_cursor: null }));
  await screen.findByRole("button", { name: /Mission two/ });
  expect(screen.getAllByRole("button", { name: /Mission one updated/ })).toHaveLength(1);
});

test("failed mission cursor keeps rows visible and retries the same cursor", async () => {
  window.history.replaceState({}, "", "/missions?state=active");
  let cursorAttempts = 0;
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("cursor=retry-me")) {
      cursorAttempts += 1;
      return cursorAttempts === 1 ? response({ detail: "temporary" }, 503) : response({ items: [mission("two")], next_cursor: null });
    }
    return response({ items: [mission("one")], next_cursor: "retry-me" });
  });
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByRole("button", { name: /Mission one/ });
  fireEvent.click(screen.getByRole("button", { name: "Load more Missions" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: /Mission one/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry loading more Missions" }));
  await screen.findByRole("button", { name: /Mission two/ });
  expect(fetcher.mock.calls.filter(([input]) => String(input).includes("cursor=retry-me"))).toHaveLength(2);
});

test("attention pagination keeps stable rows and does not request the same cursor twice", async () => {
  window.history.replaceState({}, "", "/needs-you?filter=all");
  let resolveSecond!: (value: Response) => void;
  const second = new Promise<Response>((resolve) => { resolveSecond = resolve; });
  const first = attentionItem("one");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("cursor=attention-next")) return second;
    return response({ items: [first], next_cursor: "attention-next", unread_count: 2, actionable_count: 2 });
  });
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByRole("button", { name: /Attention one/ });
  const more = screen.getByRole("button", { name: "Load more attention" });
  fireEvent.click(more);
  fireEvent.click(more);
  expect(fetcher.mock.calls.filter(([input]) => String(input).includes("cursor=attention-next"))).toHaveLength(1);
  resolveSecond(await response({ items: [{ ...first, summary: "Updated review" }, attentionItem("two")], next_cursor: null, unread_count: 2, actionable_count: 2 }));
  await screen.findByRole("button", { name: /Attention two/ });
  expect(screen.getAllByRole("button", { name: /Attention one/ })).toHaveLength(1);
  expect(screen.getByText("Updated review")).toBeInTheDocument();
});

test("mark read sends the attention id and reconciles success without resolving the source", async () => {
  window.history.replaceState({}, "", "/needs-you?filter=all");
  const item = attentionItem("attention_1");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/workspace/attention/read")) {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({ event_id: "attention_1", expected_revision: 0 });
      return response({ item: { ...item, read: true, revision: 1, actionable: true } });
    }
    return response({ items: [item], next_cursor: null, unread_count: 1, actionable_count: 1 });
  });
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Attention attention_1/ }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input]) => String(input).includes("/workspace/attention/read"))).toBe(true));
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_1?tab=work&item=task_attention_1");
  expect(await screen.findByText("This item was marked read. The Mission itself was not changed.")).toBeInTheDocument();
});

test("mark read failure stays visible and leaves the item unread after returning", async () => {
  window.history.replaceState({}, "", "/needs-you?filter=all");
  const item = attentionItem("attention_1");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).includes("/workspace/attention/read")
    ? response({ detail: "failed" }, 503)
    : response({ items: [item], next_cursor: null, unread_count: 1, actionable_count: 1 }));
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Attention attention_1/ }));
  await screen.findByRole("alert");
  expect(screen.getByText("This item is still unread. Try again from Needs you.")).toBeInTheDocument();
  window.history.pushState({}, "", "/needs-you?filter=all");
  window.dispatchEvent(new PopStateEvent("popstate"));
  const returned = await screen.findByRole("button", { name: /Attention attention_1/ });
  expect(within(returned).getByText("Unread")).toBeInTheDocument();
});

test("renders human-facing hierarchy, selection semantics, and useful empty states", async () => {
  window.history.replaceState({}, "", "/missions?state=active");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({
    items: [mission("long", { public_state: "waiting_for_human", title: "A very long Mission title that must wrap without changing the card hierarchy", needs_human_count: 2 })],
    next_cursor: null,
  }));
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  expect(screen.getByRole("button", { name: "Missions" })).toHaveAttribute("aria-current", "page");
  expect(await screen.findByText("Needs a human")).toBeInTheDocument();
  expect(screen.getByText("1 active item · 2 need a human")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Active" })).toHaveAttribute("aria-pressed", "true");
  cleanup();
  window.history.replaceState({}, "", "/needs-you?filter=actionable");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null, unread_count: 0, actionable_count: 0 }));
  render(<WorkplaceShell attentionEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  expect(await screen.findByText("You are all caught up.")).toBeInTheDocument();
  expect(screen.getByText("When a Mission needs a decision, review, or assignment, it will appear here.")).toBeInTheDocument();
});

test("search is a safe workplace affordance and navigation targets are accessible", async () => {
  window.history.replaceState({}, "", "/missions?state=active");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => response({ items: [], next_cursor: null }));
  const onSearch = vi.fn();
  render(<WorkplaceShell attentionEnabled onSearch={onSearch} onSettings={() => undefined} />);
  expect(screen.getByRole("button", { name: "Search Missions (coming soon)" })).toBeDisabled();
  expect(onSearch).not.toHaveBeenCalled();
  for (const label of ["Missions", "Needs you", "Work", "Settings"]) {
    expect(screen.getByRole("button", { name: label })).toHaveClass("workplace-nav-target");
  }
});

test("flag-on Mission detail preserves its deep link and shows crew names without raw ids", async () => {
  window.history.replaceState({}, "", "/missions/mission_close?tab=conversation&focus=message_1");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation")) return response({ items: [], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month", objective: "Prepare the verified close pack" },
      agents: [{ id: "agent_analyst", name: "Analyst", role: "Reconciliation analyst" }],
      runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 1 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_private", revision: 1, members: [{ actor_id: "human_ada", display_name: "Ada", role: "owner", actor_type: "human" }] },
      project: { id: "mission_close", name: "Close the month", objective: "Prepare the verified close pack" },
      tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled currentHumanId="human_ada" onSearch={() => undefined} onSettings={() => undefined} />);
  expect(await screen.findByRole("heading", { name: "Close the month" })).toBeInTheDocument();
  expect(screen.getByText("Analyst")).toBeInTheDocument();
  expect(screen.getByText("Ada (you)")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("agent_analyst");
  expect(document.body).not.toHaveTextContent("human_ada");
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close?tab=conversation&focus=message_1");
  fireEvent.click(screen.getByRole("button", { name: "Mention Analyst" }));
  expect(screen.getByRole("textbox", { name: "Message the Mission" })).toHaveValue("@Analyst ");
  fireEvent.click(screen.getByRole("button", { name: "Back to Missions" }));
  expect(window.location.pathname + window.location.search).toBe("/missions?state=active");
});

test("Mission conversation focuses a referenced durable message and surfaces plan recovery focus", async () => {
  const focusMessage = {
    id: "message_focus", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Review this update",
    created_at: "2026-01-02T09:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  const originalScroll = HTMLElement.prototype.scrollIntoView;
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation")) return response({ items: [focusMessage], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month", objective: "Prepare the verified close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [],
      readiness: { graph: { status: "pending_approval", revision: 2 }, crew_count: 0 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close the month", objective: "Prepare the verified close" },
      tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  window.history.replaceState({}, "", "/missions/mission_close?tab=conversation&focus=message_focus");
  const focused = render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  const article = await screen.findByRole("article");
  await waitFor(() => expect(article).toHaveFocus());
  expect(scrollIntoView).toHaveBeenCalled();
  expect(document.body).not.toHaveTextContent("message_focus");
  focused.unmount();

  window.history.replaceState({}, "", "/missions/mission_close?tab=conversation&focus=plan-approval");
  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  expect(await screen.findByText("Approve how the crew will work before assigning")).toBeInTheDocument();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: originalScroll });
});

test("older-page failure keeps the conversation and retries the same history cursor", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const newest = {
    id: "message_new", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Newest durable update",
    created_at: "2026-01-02T10:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  const older = { ...newest, id: "message_old", body: "Earlier durable update", created_at: "2026-01-02T09:00:00Z" };
  let olderAttempts = 0;
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation?") && path.includes("before=cursor-older")) {
      olderAttempts += 1;
      return olderAttempts === 1 ? response({ detail: "temporary" }, 503) : response({ items: [older], next_before: null });
    }
    if (path.includes("/conversation?")) return response({ items: [newest], next_before: "cursor-older" });
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close the month", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [],
      away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByText("Newest durable update");
  const timeline = screen.getByRole("region", { name: "Mission conversation" });
  Object.defineProperty(timeline, "scrollTop", { configurable: true, writable: true, value: 145 });
  fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load earlier messages");
  expect(screen.getByText("Newest durable update")).toBeInTheDocument();
  expect(timeline.scrollTop).toBe(145);
  fireEvent.click(screen.getByRole("button", { name: "Retry earlier messages" }));
  expect(await screen.findByText("Earlier durable update")).toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([input]) => String(input).includes("before=cursor-older"))).toHaveLength(2);
});

test("mobile Crew disclosure is bounded and does not displace the Conversation", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation")) return response({ items: [], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month" }, agents: [{ id: "agent_1", name: "Analyst", role: "Analyst" }], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 1 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [{ actor_id: "human_1", display_name: "Ada", role: "owner", actor_type: "human" }] }, project: { id: "mission_close", name: "Close", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [],
      away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByRole("heading", { name: "Close the month" });
  const crew = screen.getByRole("complementary", { name: "Mission crew" });
  const toggle = crew.querySelector<HTMLButtonElement>(".crew-mobile-toggle");
  expect(toggle).not.toBeNull();
  if (!toggle) throw new Error("Crew disclosure control is missing");
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(toggle);
  expect(toggle).toHaveTextContent("Hide");
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(crew).toHaveClass("is-open");
  expect(screen.getByRole("region", { name: "Mission conversation" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Message the Mission" })).toBeInTheDocument();
  expect(legacyStyles).toMatch(/@media\s*\(max-width:\s*620px\)[^{]*\{[\s\S]*?\.mission-crew-rail\s*\{[^}]*max-height:\s*112px[^}]*overflow-x:\s*auto[^}]*overflow-y:\s*hidden[^}]*\}[\s\S]*?\.mission-crew-rail\s*>\s*header[^}]*\{\s*display:\s*none/s);
  expect(conversationStyles).toMatch(/\.mission-conversation-workspace\s+\.mission-crew-rail\s*\{[^}]*max-height:\s*none[^}]*overflow-x:\s*hidden[^}]*width:\s*auto/s);
  expect(conversationStyles).toMatch(/\.mission-conversation-workspace\s+\.mission-crew-rail\s*>\s*header\s*\{[^}]*display:\s*flex/s);
  expect(conversationStyles).toMatch(/\.mission-conversation-workspace\s+\.crew-mobile-toggle\s*\{[^}]*display:\s*none[^}]*height:\s*auto[^}]*width:\s*auto/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*48rem\)[\s\S]*\.mission-room-layout\s*\{[^}]*grid-template-rows:\s*minmax\(44px,\s*auto\)\s+minmax\(0,\s*1fr\)/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*48rem\)[\s\S]*\.mission-crew-rail\s*\{[^}]*min-block-size:\s*44px/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*48rem\)[\s\S]*\.mission-conversation-workspace\s+\.crew-mobile-toggle\s*\{[^}]*block-size:\s*44px[^}]*height:\s*44px[^}]*min-block-size:\s*44px[^}]*min-height:\s*44px/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*48rem\)[\s\S]*\.mission-conversation-workspace\s+\.crew-mobile-toggle\s*\{[^}]*background:\s*var\(--mission-color-surface\)[^}]*color:\s*var\(--mission-color-fg\)/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*30rem\)[\s\S]*\.mission-crew-rail\s*>\s*header\s*\{[^}]*min-height:\s*44px/s);
  expect(conversationStyles).toMatch(/\.mission-crew-rail\.is-open\s*\{[^}]*position:\s*absolute[^}]*max-block-size:[^;}]+[^}]*overflow-y:\s*auto/s);
  expect(conversationStyles).toMatch(/\.mission-conversation-surface\s*\{[^}]*min-height:\s*0/s);
});

test("acknowledgement retries add and remove with exact payloads while save stays private", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const root = {
    id: "message_actions", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Review is ready",
    created_at: "2026-01-02T10:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  const actionCalls: Array<{ method: string; path: string; body: string }> = [];
  let reactionAddAttempts = 0;
  let reactionRemoveAttempts = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/conversation/messages/message_actions/reactions/check")) {
      const method = init?.method || "GET";
      actionCalls.push({ method, path, body: String(init?.body || "") });
      if (method === "PUT") {
        reactionAddAttempts += 1;
        return reactionAddAttempts === 1
          ? response({ detail: "temporary" }, 503)
          : response({ message: { ...root, reactions: [{ reaction: "check", count: 1, reacted: true }] } });
      }
      reactionRemoveAttempts += 1;
      return reactionRemoveAttempts === 1
        ? response({ detail: "temporary" }, 503)
        : response({ message: root });
    }
    if (path.endsWith("/conversation/messages/message_actions/saved")) {
      actionCalls.push({ method: init?.method || "GET", path, body: String(init?.body || "") });
      return response({ saved: init?.method === "PUT" });
    }
    if (path.includes("/conversation?")) return response({ items: [root], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [],
      away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled currentHumanId="human_current" onSearch={() => undefined} onSettings={() => undefined} />);

  fireEvent.click(await screen.findByRole("button", { name: "Acknowledge" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  const remove = await screen.findByRole("button", { name: "Remove acknowledgement · 1" });
  expect(actionCalls[0]).toEqual(actionCalls[1]);

  fireEvent.click(remove);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("button", { name: "Acknowledge" });
  expect(actionCalls[2]).toEqual(actionCalls[3]);

  fireEvent.click(screen.getByRole("button", { name: "Save message" }));
  await screen.findByRole("button", { name: "Unsave message" });
  fireEvent.click(screen.getByRole("button", { name: "Unsave message" }));
  await screen.findByRole("button", { name: "Save message" });
  expect(actionCalls.slice(-2).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
  expect(document.body).not.toHaveTextContent("human_current");
});

test("terminal durable refresh access loss clears Mission content and closes its thread", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const live: { options?: WorkspaceEventStreamOptions } = {};
  const stream: WorkspaceEventStream = async (options) => {
    live.options = options;
    options.onOpen();
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const root = {
    id: "message_private", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Private Mission context",
    created_at: "2026-01-02T10:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  let conversationReads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation/messages/message_private/replies")) return response({ items: [], next_before: null });
    if (path.includes("/conversation?")) {
      conversationReads += 1;
      return conversationReads === 1 ? response({ items: [root], next_before: null }) : response({ detail: "forbidden" }, 403);
    }
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [],
      away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled sseEnabled eventStream={stream} onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Reply to Ada" }));
  expect(await screen.findByRole("dialog", { name: "Thread" })).toBeInTheDocument();
  await waitFor(() => expect(live.options).toBeDefined());
  live.options?.onWakeUp({ id: "event_revoked", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-02T10:01:00Z" });

  expect(await screen.findByRole("alert")).toHaveTextContent("access");
  expect(screen.queryByText("Private Mission context")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Thread" })).not.toBeInTheDocument();
  expect(screen.queryByText("Close the month")).not.toBeInTheDocument();
});

test.each(["older history", "thread read", "message mutation"])("access denial during %s uses the centralized safe state", async (deniedStep) => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const root = {
    id: "message_sensitive", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Sensitive Mission context",
    created_at: "2026-01-02T10:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("before=cursor-older")) return response({ detail: "forbidden" }, 403);
    if (path.includes("/conversation/messages/message_sensitive/replies")) {
      return deniedStep === "thread read" ? response({ detail: "forbidden" }, 403) : response({ items: [], next_before: null });
    }
    if (path.includes("/conversation/messages/message_sensitive/reactions/check")) {
      return deniedStep === "message mutation" ? response({ detail: "forbidden" }, 403) : response({ message: root });
    }
    if (path.includes("/conversation?")) return response({ items: [root], next_before: deniedStep === "older history" ? "cursor-older" : null });
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "Sensitive Mission" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Sensitive Mission", objective: "Private" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    throw new Error(`Unexpected request ${path} ${init?.method || "GET"}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByText("Sensitive Mission context");

  if (deniedStep === "older history") fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }));
  if (deniedStep === "thread read") fireEvent.click(screen.getByRole("button", { name: "Reply to Ada" }));
  if (deniedStep === "message mutation") fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByText("Sensitive Mission context")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Thread" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Message the Mission" })).not.toBeInTheDocument();
});

test("live reconciliation removes a visible root that is absent from the authoritative displayed range", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const live: { options?: WorkspaceEventStreamOptions } = {};
  const stream: WorkspaceEventStream = async (options) => {
    live.options = options;
    options.onOpen();
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const first = {
    id: "message_removed", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "No longer visible",
    created_at: "2026-01-02T09:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  const second = { ...first, id: "message_kept", body: "Before refresh", created_at: "2026-01-02T10:00:00Z" };
  let conversationReads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation?")) {
      conversationReads += 1;
      return conversationReads === 1
        ? response({ items: [first, second], next_before: null })
        : response({ items: [{ ...second, body: "After authoritative refresh" }], next_before: null });
    }
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "Close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled sseEnabled eventStream={stream} onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByText("No longer visible");
  await waitFor(() => expect(live.options).toBeDefined());
  live.options?.onWakeUp({ id: "event_refresh", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-02T10:01:00Z" });
  await screen.findByText("After authoritative refresh");
  expect(screen.queryByText("No longer visible")).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
});

test("long disconnect exhausts authoritative pages and never keeps a removed cached root", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const live: { options?: WorkspaceEventStreamOptions } = {};
  const stream: WorkspaceEventStream = async (options) => {
    live.options = options;
    options.onOpen();
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const root = {
    id: "message_removed_long_ago", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Removed while disconnected",
    created_at: "2026-01-01T08:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  const fresh = (id: string, at: string) => ({ ...root, id, body: `Fresh ${id}`, created_at: at });
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.includes("/conversation?")) {
      reads.push(path);
      if (reads.length === 1) return response({ items: [root], next_before: null });
      if (!path.includes("before=")) return response({ items: [fresh("page_3", "2026-01-04T08:00:00Z")], next_before: "cursor_2" });
      if (path.includes("before=cursor_2")) return response({ items: [fresh("page_2", "2026-01-03T08:00:00Z")], next_before: "cursor_1" });
      if (path.includes("before=cursor_1")) return response({ items: [fresh("page_1", "2026-01-02T08:00:00Z")], next_before: null });
    }
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "Close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled sseEnabled eventStream={stream} onSearch={() => undefined} onSettings={() => undefined} />);
  await screen.findByText("Removed while disconnected");
  await waitFor(() => expect(live.options).toBeDefined());
  live.options?.onWakeUp({ id: "event_long_disconnect", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-04T09:00:00Z" });

  await screen.findByText("Fresh page_1");
  expect(screen.queryByText("Removed while disconnected")).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(3);
  expect(reads).toHaveLength(4);
});

test("lost reply accepted by live refresh is not counted twice when exact retry returns", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const live: { options?: WorkspaceEventStreamOptions } = {};
  const stream: WorkspaceEventStream = async (options) => {
    live.options = options;
    options.onOpen();
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const reply = {
    id: "reply_accepted", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_current", kind: "human", display_name: "You", avatar_url: null }, body: "Accepted once",
    created_at: "2026-01-02T10:01:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  const root = {
    id: "message_root", mission_id: "mission_close", kind: "human_message",
    author: { id: "human_ada", kind: "human", display_name: "Ada", avatar_url: null }, body: "Root message",
    created_at: "2026-01-02T10:00:00Z", edited_at: null, thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
  let conversationReads = 0;
  let replyPosts = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/conversation/messages/message_root/replies") && init?.method === "POST") {
      replyPosts += 1;
      return replyPosts === 1 ? response({ detail: "lost response" }, 503) : response({ message: reply });
    }
    if (path.includes("/conversation/messages/message_root/replies")) return response({ items: [], next_before: null });
    if (path.includes("/conversation?")) {
      conversationReads += 1;
      return response({ items: [conversationReads === 1 ? root : { ...root, thread: { reply_count: 1, latest_replies: [reply] } }], next_before: null });
    }
    if (path.endsWith("/projects/mission_close/mission")) return response({ mission: { title: "Close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [], readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 } });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({ room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Close", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled sseEnabled eventStream={stream} currentHumanId="human_current" onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Reply to Ada" }));
  const input = await screen.findByRole("textbox", { name: "Reply in thread" });
  fireEvent.change(input, { target: { value: "Accepted once" } });
  fireEvent.click(screen.getByRole("button", { name: "Reply" }));
  await screen.findByText("Your reply is safe to try again.");
  await waitFor(() => expect(live.options).toBeDefined());
  live.options?.onWakeUp({ id: "event_reply", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-02T10:01:00Z" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Reply to Ada" })).toHaveTextContent("Thread · 1"));
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(replyPosts).toBe(2));
  expect(screen.getByRole("button", { name: "Reply to Ada" })).toHaveTextContent("Thread · 1");
});

test("workplace layout keeps one scroll owner and a compact mobile navigation without gradients", () => {
  expect(workplaceStyles).not.toMatch(/gradient\s*\(/i);
  expect(attentionStyles).not.toMatch(/gradient\s*\(/i);
  expect(workplaceStyles).toMatch(/\.workplace-shell\s*\{[^}]*overflow:\s*hidden/s);
  expect(workplaceStyles).toMatch(/\.workplace-rail\s*\{[^}]*flex:\s*0\s+0\s+4rem/s);
  expect(workplaceStyles).toMatch(/\.workplace-main\s*\{[^}]*overflow-y:\s*auto/s);
  expect(workplaceStyles).toMatch(/@media\s*\(max-width:\s*48rem\)[\s\S]*\.workplace-shell\s*\{\s*flex-direction:\s*column-reverse/s);
  expect(workplaceStyles).toMatch(/min-height:\s*2\.5rem/);
  expect(workplaceStyles).toMatch(/overflow-wrap:\s*anywhere/);
  expect(conversationStyles).not.toMatch(/gradient\s*\(/i);
  expect(conversationStyles).toMatch(/\.mission-room-layout\s*\{[^}]*overflow:\s*hidden/s);
  expect(conversationStyles).toMatch(/\.mission-room-layout\s*\{[^}]*grid-template-columns:\s*13\.5rem\s+minmax\(0,\s*1fr\)/s);
  expect(conversationStyles).toMatch(/\.crew-member\s*\{[^}]*grid-template-columns:\s*1\.75rem\s+minmax\(0,\s*1fr\)\s+2rem[^}]*padding-block:\s*0\.375rem/s);
  expect(conversationStyles).toMatch(/\.conversation-message\.is-progress\s*\{[^}]*grid-template-columns:\s*1\.75rem\s+minmax\(0,\s*1fr\)/s);
  expect(conversationStyles).toMatch(/\.conversation-composer\s*\{[^}]*max-width:\s*54rem/s);
  expect(conversationStyles).toMatch(/\.conversation-timeline\s*\{[^}]*overflow-y:\s*auto/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*48rem\)[\s\S]*\.mission-room-layout\s*\{[^}]*grid-template-columns:\s*1fr/s);
  expect(conversationStyles).toMatch(/@media\s*\(max-width:\s*30rem\)[\s\S]*\.mission-crew-rail\s*\{[^}]*grid-template-columns:\s*1fr/s);
  expect(conversationStyles).toMatch(/overflow-wrap:\s*anywhere/);
});
