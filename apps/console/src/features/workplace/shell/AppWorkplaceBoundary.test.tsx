import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { WorkplaceFlags } from "../../../api";

vi.mock("../../../components/Landing", () => ({
  Landing: ({ projects = [], onOpenProject, onPrompt, onBuild, onLogin }: {
    projects?: Array<{ id: string }>;
    onOpenProject?: (id: string) => void;
    onPrompt?: (value: string) => void;
    onBuild?: () => void;
    onLogin?: () => void;
  }) => <main>
    <h1>Legacy landing</h1>
    <input aria-label="Mission outcome" onChange={(event) => onPrompt?.(event.currentTarget.value)} />
    <button type="button" onClick={onBuild}>Start legacy Mission</button>
    <button type="button" onClick={onLogin}>Open landing account</button>
    {projects.map((project) => <button type="button" key={project.id} onClick={() => onOpenProject?.(project.id)}>Open legacy project</button>)}
  </main>,
}));
vi.mock("../../../components/AgentShell", () => ({
  AgentShell: ({ busy, onApprove }: { busy: boolean; onApprove?: () => void }) => <main>
    <p>Legacy work is {busy ? "live" : "idle"}</p>
    <button type="button" onClick={onApprove}>Approve legacy plan</button>
  </main>,
}));
vi.mock("../../../components/Sidebar", () => ({
  Sidebar: ({ onAccount }: { onAccount?: () => void }) => <button type="button" onClick={onAccount}>Open legacy account</button>,
}));
vi.mock("../../../components/PreviewDrawer", () => ({ PreviewDrawer: () => null }));
vi.mock("../../../components/ui/ResizableSplit", () => ({
  ResizableSplit: ({ left }: { left: ReactNode }) => <>{left}</>,
}));
vi.mock("../../../components/ProfileManageModal", () => ({
  ProfileManageModal: ({ open, onClose, onTenant }: { open: boolean; onClose?: () => void; onTenant?: (id: string) => void }) => open ? <div role="dialog" aria-label="Account">
    <button type="button" onClick={() => onTenant?.("tenant_b")}>Switch workspace</button>
    <button type="button" onClick={onClose}>Close account</button>
  </div> : null,
}));

import App from "../../../App";

const flags = (enabled: boolean): WorkplaceFlags => ({
  workplace_shell_v1: enabled,
  workplace_attention_v1: enabled,
  workplace_conversation_v1: enabled,
  workplace_files_v1: false,
  workplace_preview_origin_v1: false,
  workplace_sse_v1: false,
  workplace_bootstrap_v1: false,
});

function isLegacyProjectRequest(input: unknown): boolean {
  const pathname = new URL(String(input), "http://missions.test").pathname.replace(/^\/api/, "");
  return pathname === "/projects"
    || /^\/projects\/[^/]+(?:\/job|\/files|\/events|\/approve)?$/.test(pathname);
}

const response = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), {
  status: 200,
  headers: { "Content-Type": "application/json" },
}));

const eventSources: Array<{ url: string; closed: boolean }> = [];

class FakeEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  readonly record: { url: string; closed: boolean };

  constructor(url: string | URL) {
    this.record = { url: String(url), closed: false };
    eventSources.push(this.record);
  }

  close() {
    this.record.closed = true;
  }
}

beforeEach(() => {
  eventSources.length = 0;
  vi.stubGlobal("EventSource", FakeEventSource);
  localStorage.setItem("simulacra_token", "test-token");
  localStorage.setItem("simulacra_tenant_id", "tenant_a");
  window.history.replaceState({}, "", "/missions?state=active");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

test("enabled login and workspace switch resolve flags before any legacy project request", async () => {
  let meCalls = 0;
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.endsWith("/auth/me")) {
      meCalls += 1;
      const tenant = meCalls === 1 ? "tenant_a" : "tenant_b";
      if (meCalls === 2) expect(new Headers(init?.headers).get("X-Tenant-Id")).toBe("tenant_b");
      return response({
        user: { id: "human_1", email: "human@example.com", name: "Human" },
        tenant_id: tenant,
        role: "owner",
        tenants: [{ id: "tenant_a", name: "A" }, { id: "tenant_b", name: "B" }],
        workplace_flags: flags(true),
      });
    }
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    if (path.includes("/workspace/attention")) return response({ items: [], next_cursor: null, unread_count: 0, actionable_count: 0 });
    if (path.includes("/projects")) return response({ projects: [] });
    throw new Error(`Unexpected request ${path}`);
  });

  render(<App />);
  await screen.findByRole("heading", { name: "Missions" });
  expect(screen.queryByRole("heading", { name: "Legacy landing" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(await screen.findByRole("button", { name: "Switch workspace" }));
  await waitFor(() => expect(meCalls).toBe(2));
  await screen.findByRole("heading", { name: "Missions" });
  expect(fetcher.mock.calls.some(([input]) => isLegacyProjectRequest(input))).toBe(false);
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/job"))).toBe(false);
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/approve"))).toBe(false);
});

test("disabled flag preserves the legacy entry path", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/auth/me")) return response({
      user: { id: "human_1", email: "human@example.com", name: "Human" },
      tenant_id: "tenant_a",
      role: "owner",
      tenants: [{ id: "tenant_a", name: "A" }],
      workplace_flags: flags(false),
    });
    if (path.includes("/projects")) return response({ projects: [] });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<App />);
  await screen.findByRole("heading", { name: "Legacy landing" });
  await waitFor(() => expect(fetcher.mock.calls.some(([input]) => String(input).includes("/projects"))).toBe(true));
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/missions?"))).toBe(false);
});

test("flag-on Mission detail uses the durable conversation and passes the current human", async () => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.endsWith("/auth/me")) return response({
      user: { id: "human_1", email: "human@example.com", name: "Human" },
      tenant_id: "tenant_a",
      role: "owner",
      tenants: [{ id: "tenant_a", name: "A" }],
      workplace_flags: flags(true),
    });
    if (path.includes("/projects/mission_close/conversation/messages")) {
      expect(init?.method).toBe("POST");
      return response({
        message: {
          id: "message_1", mission_id: "mission_close", kind: "human_message",
          author: { id: "human_1", kind: "human", display_name: "Human", avatar_url: null },
          body: "Status update", created_at: "2026-01-02T09:00:00Z", edited_at: null,
          thread: { reply_count: 0, latest_replies: [] }, reactions: [], saved: false,
          links: { work_item_id: null, run_id: null, output_id: null },
        },
        work_item: null,
      });
    }
    if (path.includes("/projects/mission_close/conversation")) return response({ items: [], next_before: null });
    if (path.endsWith("/projects/mission_close/mission")) return response({
      mission: { title: "Close the month", objective: "Prepare a verified close" }, agents: [], runs: [], triggers: [], deliverables: [], events: [], approvals: [],
      readiness: { graph: { status: "approved", revision: 1 }, crew_count: 0 },
    });
    if (path.endsWith("/projects/mission_close/cmul8/room")) return response({
      room: { id: "room_1", revision: 1, members: [{ actor_id: "human_1", display_name: "Human", role: "owner", actor_type: "human" }] },
      project: { id: "mission_close", name: "Close the month", objective: "Prepare a verified close" }, tasks: [], comments: [], reviews: [], events: [],
      away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    });
    throw new Error(`Unexpected request ${path}`);
  });
  render(<App />);
  expect(await screen.findByText("Human (you)")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Message the Mission" }), { target: { value: "Status update" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input]) => String(input).includes("/conversation/messages"))).toBe(true));
  expect(fetcher.mock.calls.some(([input]) => isLegacyProjectRequest(input))).toBe(false);
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/mission/runs") || String(input).includes("/cmul8/comments"))).toBe(false);
});

test("flag-off keeps live polling, stale completion, and legacy plan approval", async () => {
  const project = {
    id: "project_1",
    prompt: "Prepare close",
    goal: "Close books",
    phase: "plan",
    plan_approved: false,
    status: "planning",
    gates_status: "pending",
    deployed: false,
    deploy_url: null,
    chat: [],
    app_config: { title: "Monthly close", subtitle: "" },
    row_count: 0,
    checkpoints: [],
    active_checkpoint: 0,
    plan_preview: {},
    job: { id: "job_1", status: "running" },
  };
  const snapshot = { project, preview_data: { columns: [], rows: [], row_count: 0 }, preview_url: null, job: project.job };
  let statusCalls = 0;
  let poller: (() => Promise<void>) | undefined;
  vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler, timeout?: number) => {
    if (timeout === 1500) {
      poller = handler as () => Promise<void>;
      return 77;
    }
    return 76;
  });
  vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.endsWith("/auth/me")) return response({
      user: { id: "human_1", email: "human@example.com", name: "Human" },
      tenant_id: "tenant_a",
      role: "owner",
      tenants: [{ id: "tenant_a", name: "A" }],
      workplace_flags: flags(false),
    });
    if (path.endsWith("/projects")) return response({ projects: [project] });
    if (path.endsWith("/projects/project_1/job")) {
      statusCalls += 1;
      return response(statusCalls === 1
        ? { job: { id: "job_1", status: "running" }, live: true }
        : { job: { id: "job_1", status: "running" }, live: false });
    }
    if (path.endsWith("/projects/project_1/approve")) {
      expect(init?.method).toBe("POST");
      return response({ ...snapshot, job_id: "job_2", job: { id: "job_2", status: "running" } });
    }
    if (path.endsWith("/projects/project_1/files")) return response({ files: [] });
    if (path.endsWith("/projects/project_1/events")) return response({ events: [] });
    if (path.endsWith("/projects/project_1")) return response(snapshot);
    throw new Error(`Unexpected request ${path}`);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Open legacy project" }));
  await waitFor(() => expect(statusCalls).toBe(1));
  expect(await screen.findByText("Legacy work is live")).toBeInTheDocument();
  expect(poller).toBeDefined();

  await act(async () => { await poller?.(); });
  expect(statusCalls).toBe(2);
  expect(await screen.findByText("Legacy work is idle")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Approve legacy plan" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/projects/project_1/approve"))).toBe(true));
  expect(await screen.findByText("Legacy work is live")).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/missions?"))).toBe(false);
});

test("legacy workspace switch stops old project activity before target flags resolve", async () => {
  const project = {
    id: "project_1",
    prompt: "Prepare close",
    goal: "Close books",
    phase: "plan",
    plan_approved: false,
    status: "planning",
    gates_status: "pending",
    deployed: false,
    deploy_url: null,
    chat: [],
    app_config: { title: "Monthly close", subtitle: "" },
    row_count: 0,
    checkpoints: [],
    active_checkpoint: 0,
    plan_preview: {},
    job: { id: "job_1", status: "running" },
  };
  const snapshot = { project, preview_data: { columns: [], rows: [], row_count: 0 }, preview_url: null, job: project.job };
  let resolveTargetMe!: (value: Response) => void;
  const targetMe = new Promise<Response>((resolve) => { resolveTargetMe = resolve; });
  let meCalls = 0;
  let poller: (() => Promise<void>) | undefined;
  vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler, timeout?: number) => {
    if (timeout === 1500) {
      poller = handler as () => Promise<void>;
      return 88;
    }
    return 87;
  });
  const clearTimer = vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    const tenant = new Headers(init?.headers).get("X-Tenant-Id");
    if (path.endsWith("/auth/me")) {
      meCalls += 1;
      if (meCalls === 2) return targetMe;
      return response({
        user: { id: "human_1", email: "human@example.com", name: "Human" },
        tenant_id: "tenant_a",
        role: "owner",
        tenants: [{ id: "tenant_a", name: "A" }, { id: "tenant_b", name: "B" }],
        workplace_flags: flags(false),
      });
    }
    if (path.endsWith("/projects")) return response({ projects: [project] });
    if (path.endsWith("/projects/project_1/job")) return response({ job: project.job, live: true });
    if (path.endsWith("/projects/project_1/files")) return response({ files: [] });
    if (path.endsWith("/projects/project_1/events")) return response({ events: [] });
    if (path.endsWith("/projects/project_1")) return response(snapshot);
    throw new Error(`Unexpected ${tenant || "no-tenant"} request ${path}`);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Open legacy project" }));
  expect(await screen.findByText("Legacy work is live")).toBeInTheDocument();
  await act(async () => { await Promise.resolve(); });
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/projects/project_1/files"))).toBe(true);
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/projects/project_1/events"))).toBe(true);
  expect(eventSources).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "Open legacy account" }));
  fireEvent.click(await screen.findByRole("button", { name: "Switch workspace" }));
  await waitFor(() => expect(meCalls).toBe(2));
  expect(clearTimer).toHaveBeenCalledWith(88);
  expect(eventSources[0]?.closed).toBe(true);

  await act(async () => { await poller?.(); });
  const targetLegacyCallsBeforeFlags = fetcher.mock.calls.filter(([input, init]) => {
    return new Headers(init?.headers).get("X-Tenant-Id") === "tenant_b" && isLegacyProjectRequest(input);
  });
  expect(targetLegacyCallsBeforeFlags).toHaveLength(0);

  resolveTargetMe(await response({
    user: { id: "human_1", email: "human@example.com", name: "Human" },
    tenant_id: "tenant_b",
    role: "owner",
    tenants: [{ id: "tenant_a", name: "A" }, { id: "tenant_b", name: "B" }],
    workplace_flags: flags(true),
  }));
  expect(await screen.findByRole("main", { name: "Workplace" })).toBeInTheDocument();
  const targetLegacyCalls = fetcher.mock.calls.filter(([input, init]) => {
    const path = String(input);
    return new Headers(init?.headers).get("X-Tenant-Id") === "tenant_b"
      && (/\/projects(?:\/|$)/.test(path) || /\/(?:files|events|job)$/.test(path));
  });
  expect(targetLegacyCalls).toHaveLength(0);
});

test("a delayed legacy create cannot continue in the newly selected workplace", async () => {
  const project = {
    id: "project_delayed",
    prompt: "Build report",
    goal: "Build report",
    phase: "plan",
    plan_approved: false,
    status: "planning",
    gates_status: "pending",
    deployed: false,
    deploy_url: null,
    chat: [],
    app_config: { title: "Delayed report", subtitle: "" },
    row_count: 0,
    checkpoints: [],
    active_checkpoint: 0,
    plan_preview: {},
    job: { id: "job_delayed", status: "idle" },
  };
  const snapshot = { project, preview_data: { columns: [], rows: [], row_count: 0 }, preview_url: null, job: project.job };
  let resolveCreate!: (value: Response) => void;
  const delayedCreate = new Promise<Response>((resolve) => { resolveCreate = resolve; });
  let meCalls = 0;
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    const method = init?.method || "GET";
    if (path.endsWith("/auth/me")) {
      meCalls += 1;
      const enabled = meCalls > 1;
      return response({
        user: { id: "human_1", email: "human@example.com", name: "Human" },
        tenant_id: enabled ? "tenant_b" : "tenant_a",
        role: "owner",
        tenants: [{ id: "tenant_a", name: "A" }, { id: "tenant_b", name: "B" }],
        workplace_flags: flags(enabled),
      });
    }
    if (path.endsWith("/projects") && method === "GET") return response({ projects: [] });
    if (path.endsWith("/projects") && method === "POST") return delayedCreate;
    if (path.includes("/missions?")) return response({ items: [], next_cursor: null });
    if (path.includes("/workspace/attention")) return response({ items: [], next_cursor: null, unread_count: 0, actionable_count: 0 });
    if (path.endsWith("/projects/project_delayed/mission")) return response({});
    throw new Error(`Unexpected request ${path}`);
  });

  render(<App />);
  fireEvent.change(await screen.findByRole("textbox", { name: "Mission outcome" }), { target: { value: "Build report" } });
  fireEvent.click(screen.getByRole("button", { name: "Start legacy Mission" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) => String(input).endsWith("/projects") && init?.method === "POST")).toBe(true));

  fireEvent.click(screen.getByRole("button", { name: "Open landing account" }));
  fireEvent.click(await screen.findByRole("button", { name: "Switch workspace" }));
  expect(await screen.findByRole("main", { name: "Workplace" })).toBeInTheDocument();

  await act(async () => { resolveCreate(await response(snapshot)); });
  await act(async () => { await Promise.resolve(); });
  const targetFollowUps = fetcher.mock.calls.filter(([input, init]) => {
    const path = String(input);
    return new Headers(init?.headers).get("X-Tenant-Id") === "tenant_b" && path.includes("/projects/");
  });
  expect(targetFollowUps).toHaveLength(0);
  expect(screen.getByRole("main", { name: "Workplace" })).toBeInTheDocument();
});

test("settings reload opens account and close restores a coherent Missions URL", async () => {
  window.history.replaceState({}, "", "/settings/account");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = String(input);
    if (path.endsWith("/auth/me")) return response({
      user: { id: "human_1", email: "human@example.com", name: "Human" },
      tenant_id: "tenant_a",
      role: "owner",
      tenants: [{ id: "tenant_a", name: "A" }],
      workplace_flags: flags(true),
    });
    return response({ items: [], next_cursor: null });
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Close account" }));
  expect(window.location.pathname + window.location.search).toBe("/missions?state=active");
  await waitFor(() => expect(screen.getByRole("button", { name: "Settings" })).toHaveFocus());
});
