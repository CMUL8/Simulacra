import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { WorkplaceShell } from "../shell/WorkplaceShell";
import { ConversationComposer } from "./ConversationComposer";
import { ApiError, type ConversationSendRequest, type ConversationSendResponse } from "../../../api";

const agents = [
  { id: "agent_analyst", name: "Analyst", role: "Reconciliation analyst" },
  { id: "agent_editor", name: "Editor", role: "Report editor" },
];

const humans = [
  { id: "human_ada", display_name: "Ada", role: "owner" },
  { id: "human_maya", display_name: "Maya", role: "reviewer" },
];

const sentMessage = {
  id: "message_1",
  mission_id: "mission_close",
  kind: "assignment_created",
  author: { id: "human_ada", kind: "human" as const, display_name: "Ada", avatar_url: null },
  body: "Reconcile the close",
  created_at: "2026-01-02T09:00:00Z",
  edited_at: null,
  thread: { reply_count: 0, latest_replies: [] },
  reactions: [],
  saved: false,
  links: { work_item_id: "work_1", run_id: null, output_id: null },
};

const okResponse: ConversationSendResponse = {
  message: sentMessage,
  work_item: {
    id: "work_1",
    title: "Reconcile the close",
    state: "queued",
    assignee_agent_ids: ["agent_analyst", "agent_editor"],
    reviewer_human_ids: ["human_maya"],
    allowed_actions: ["open"],
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

test("composer_shows_assignment_preview_and_uses_agent_ids", async () => {
  let resolveSend!: (value: ConversationSendResponse) => void;
  const request = new Promise<ConversationSendResponse>((resolve) => { resolveSend = resolve; });
  const send = vi.fn((_missionId: string, _payload: ConversationSendRequest) => request);
  render(<ConversationComposer
    missionId="mission_close"
    agents={agents}
    humans={humans}
    requestIdFactory={() => "request_assignment"}
    send={send}
    onSent={() => undefined}
    onAccessLost={() => undefined}
  />);

  const input = screen.getByRole("textbox", { name: "Message the Mission" });
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: /Analyst/ }));
  fireEvent.change(input, { target: { value: "@Analyst @" } });
  fireEvent.click(screen.getByRole("option", { name: /Editor/ }));
  fireEvent.change(input, { target: { value: "@Analyst @Editor reconcile the close" } });

  expect(screen.getByRole("button", { name: "Assign work mode" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Analyst → Editor")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Assign work" }));
  fireEvent.click(screen.getByRole("button", { name: "Assigning…" }));
  expect(send).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledWith("mission_close", {
    client_request_id: "request_assignment",
    body: "@Analyst @Editor reconcile the close",
    mode: "assignment",
    assignee_agent_ids: ["agent_analyst", "agent_editor"],
    reviewer_human_ids: [],
    source_message_id: null,
  });
  expect(JSON.stringify(send.mock.calls[0]?.[1])).not.toContain("Reconciliation analyst");
  resolveSend(okResponse);
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Message the Mission" })).toHaveValue(""));
});

test("lost response retry reuses the exact request id and frozen payload", async () => {
  const send = vi.fn()
    .mockRejectedValueOnce(new Error("network disconnected"))
    .mockResolvedValueOnce(okResponse);
  render(<ConversationComposer
    missionId="mission_close"
    agents={agents}
    humans={humans}
    requestIdFactory={() => "request_retry"}
    send={send}
    onSent={() => undefined}
    onAccessLost={() => undefined}
  />);

  const input = screen.getByRole("textbox", { name: "Message the Mission" });
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: /Analyst/ }));
  fireEvent.change(input, { target: { value: "@Analyst prepare the pack" } });
  fireEvent.change(input, { target: { value: "@Analyst @" } });
  fireEvent.click(screen.getByRole("option", { name: /Maya/ }));
  fireEvent.change(input, { target: { value: "@Analyst @Maya prepare the pack" } });
  fireEvent.click(screen.getByRole("button", { name: "Assign work" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Your work is safe to try again");
  expect(input).toBeDisabled();
  const firstPayload = send.mock.calls[0]?.[1];
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  expect(send.mock.calls[1]?.[1]).toBe(firstPayload);
  expect(send.mock.calls[1]?.[1]).toEqual({
    client_request_id: "request_retry",
    body: "@Analyst @Maya prepare the pack",
    mode: "assignment",
    assignee_agent_ids: ["agent_analyst"],
    reviewer_human_ids: ["human_maya"],
    source_message_id: null,
  });
});

test("failed send can return to the draft and the edited submit gets a new request id", async () => {
  const ids = ["request_a", "request_b"];
  const send = vi.fn()
    .mockRejectedValueOnce(new Error("response lost"))
    .mockResolvedValueOnce(okResponse);
  render(<ConversationComposer
    missionId="mission_close"
    agents={agents}
    humans={humans}
    requestIdFactory={() => ids.shift() || "unexpected_request"}
    send={send}
    onSent={() => undefined}
    onAccessLost={() => undefined}
  />);

  const input = screen.getByRole("textbox", { name: "Message the Mission" });
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: /Analyst/ }));
  fireEvent.change(input, { target: { value: "@Analyst prepare the first pack" } });
  fireEvent.click(screen.getByRole("button", { name: "Assign work" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(send.mock.calls[0]?.[1]).toMatchObject({ client_request_id: "request_a", assignee_agent_ids: ["agent_analyst"] });

  fireEvent.click(screen.getByRole("button", { name: "Edit message" }));
  expect(input).toBeEnabled();
  await waitFor(() => expect(input).toHaveFocus());
  expect(input).toHaveValue("@Analyst prepare the first pack");
  expect(screen.getByText("Analyst")).toBeInTheDocument();
  fireEvent.change(input, { target: { value: "@Analyst prepare the corrected pack" } });
  fireEvent.click(screen.getByRole("button", { name: "Assign work" }));

  await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  expect(send.mock.calls[1]?.[1]).toEqual({
    client_request_id: "request_b",
    body: "@Analyst prepare the corrected pack",
    mode: "assignment",
    assignee_agent_ids: ["agent_analyst"],
    reviewer_human_ids: [],
    source_message_id: null,
  });
});

test("message mode never infers assignment from arbitrary at text", async () => {
  const send = vi.fn().mockResolvedValue({ ...okResponse, work_item: null, message: { ...sentMessage, kind: "human_message" } });
  render(<ConversationComposer
    missionId="mission_close"
    agents={agents}
    humans={humans}
    requestIdFactory={() => "request_message"}
    send={send}
    onSent={() => undefined}
    onAccessLost={() => undefined}
  />);
  expect(document.querySelector(".composer-command")).not.toBeNull();
  expect(document.querySelector(".composer-command.is-message-only")).not.toBeNull();
  expect(screen.queryByText("Everyone in this Mission can follow the conversation.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Message mode" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Assign work mode" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send message" })).toHaveClass("is-icon-only");
  fireEvent.change(screen.getByRole("textbox", { name: "Message the Mission" }), {
    target: { value: "@Analyst is plain text without a durable selection" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(send.mock.calls[0]?.[1]).toMatchObject({
    mode: "message",
    assignee_agent_ids: [],
    reviewer_human_ids: [],
  });
});

test("assignment waits until a human approves how the crew will work", () => {
  const send = vi.fn();
  render(<ConversationComposer
    missionId="mission_close"
    agents={agents}
    humans={humans}
    assignmentEnabled={false}
    send={send}
    onSent={() => undefined}
    onAccessLost={() => undefined}
  />);
  const input = screen.getByRole("textbox", { name: "Message the Mission" });
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: /Analyst/ }));
  fireEvent.change(input, { target: { value: "@Analyst prepare the pack" } });
  expect(screen.getByRole("button", { name: "Assign work" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Approve how the crew will work before assigning" })).toHaveAttribute("href", "/missions/mission_close?tab=conversation&focus=plan-approval");
  fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
  expect(send).not.toHaveBeenCalled();
});

test("legacy_agent_shell_submission_is_removed_or_delegates_to_conversation_composer", async () => {
  window.history.replaceState({}, "", "/missions/mission_close?tab=conversation&focus=message_1");
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.includes("/projects/mission_close/conversation/messages")) {
      expect(init?.method).toBe("POST");
      return Promise.resolve(new Response(JSON.stringify({ ...okResponse, work_item: null, message: { ...sentMessage, kind: "human_message" } }), { status: 200 }));
    }
    if (path.includes("/projects/mission_close/conversation")) return Promise.resolve(new Response(JSON.stringify({ items: [], next_before: null }), { status: 200 }));
    if (path.includes("/projects/mission_close/mission")) return Promise.resolve(new Response(JSON.stringify({
      mission: { title: "Close the month" }, agents, readiness: { graph: { status: "approved", revision: 1 }, crew_count: 2 },
    }), { status: 200 }));
    if (path.includes("/projects/mission_close/cmul8/room")) return Promise.resolve(new Response(JSON.stringify({
      room: { id: "room_1", revision: 1, members: humans.map((human) => ({ actor_id: human.id, display_name: human.display_name, role: human.role, actor_type: "human" })) },
      project: { id: "mission_close", name: "Close the month", objective: "Close" }, tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    }), { status: 200 }));
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  const input = await screen.findByRole("textbox", { name: "Message the Mission" });
  fireEvent.change(input, { target: { value: "Status update" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input]) => String(input).includes("/conversation/messages"))).toBe(true));
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/mission/runs"))).toBe(false);
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/cmul8/comments"))).toBe(false);
  expect(window.location.pathname + window.location.search).toBe("/missions/mission_close?tab=conversation&focus=message_1");
});

test.each([
  { mode: "message", status: 403 },
  { mode: "assignment", status: 401 },
])("$mode send access loss clears protected Mission content and the open thread", async ({ mode, status }) => {
  window.history.replaceState({}, "", "/missions/mission_close/conversation");
  const root = { ...sentMessage, id: "message_sensitive", kind: "human_message", body: "Protected Mission context", thread: { reply_count: 0, latest_replies: [] }, links: { work_item_id: null, run_id: null, output_id: null } };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    if (path.endsWith("/projects/mission_close/conversation/messages") && init?.method === "POST") {
      const payload = JSON.parse(String(init.body)) as ConversationSendRequest;
      expect(payload.mode).toBe(mode);
      return Promise.resolve(new Response(JSON.stringify({ detail: "access denied" }), { status }));
    }
    if (path.includes("/conversation/messages/message_sensitive/replies")) return Promise.resolve(new Response(JSON.stringify({ items: [], next_before: null }), { status: 200 }));
    if (path.includes("/projects/mission_close/conversation?")) return Promise.resolve(new Response(JSON.stringify({ items: [root], next_before: null }), { status: 200 }));
    if (path.endsWith("/projects/mission_close/mission")) return Promise.resolve(new Response(JSON.stringify({
      mission: { title: "Sensitive Mission" }, agents: mode === "assignment" ? [agents[0]] : [], runs: [], triggers: [], deliverables: [], events: [], approvals: [],
      readiness: { graph: { status: "approved", revision: 1 }, crew_count: mode === "assignment" ? 1 : 0 },
    }), { status: 200 }));
    if (path.endsWith("/projects/mission_close/cmul8/room")) return Promise.resolve(new Response(JSON.stringify({
      room: { id: "room_1", revision: 1, members: [] }, project: { id: "mission_close", name: "Sensitive Mission", objective: "Private" },
      tasks: [], comments: [], reviews: [], events: [], away: { total: 0, unread: 0, counts: {}, highlights: [] }, permissions: {}, presence: [],
    }), { status: 200 }));
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WorkplaceShell attentionEnabled conversationEnabled onSearch={() => undefined} onSettings={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Reply to Ada" }));
  await screen.findByRole("dialog", { name: "Thread" });
  const input = screen.getByRole("textbox", { name: "Message the Mission" });
  if (mode === "assignment") {
    fireEvent.change(input, { target: { value: "@" } });
    fireEvent.click(screen.getByRole("option", { name: /Analyst/ }));
    fireEvent.change(input, { target: { value: "@Analyst prepare the pack" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign work" }));
  } else {
    fireEvent.change(input, { target: { value: "Share the update" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  }

  expect(await screen.findByRole("alert")).toHaveTextContent("no longer have access");
  expect(screen.queryByText("Protected Mission context")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Thread" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Message the Mission" })).not.toBeInTheDocument();
});

test("a deferred send success after access loss cannot repopulate the Mission", async () => {
  let resolveSend!: (value: ConversationSendResponse) => void;
  let revokeAccess!: () => void;
  const request = new Promise<ConversationSendResponse>((resolve) => { resolveSend = resolve; });
  const onSent = vi.fn();
  function Harness() {
    const [lost, setLost] = useState(false);
    revokeAccess = () => setLost(true);
    return lost ? <div role="alert">Access lost</div> : <ConversationComposer
      missionId="mission_close"
      agents={[]}
      humans={[]}
      send={() => request}
      onSent={onSent}
      onAccessLost={() => setLost(true)}
    />;
  }
  render(<Harness />);
  fireEvent.change(screen.getByRole("textbox", { name: "Message the Mission" }), { target: { value: "Pending update" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  act(() => revokeAccess());
  expect(screen.getByRole("alert")).toHaveTextContent("Access lost");
  await act(async () => resolveSend({ ...okResponse, work_item: null }));
  expect(onSent).not.toHaveBeenCalled();
  expect(screen.queryByText("Pending update")).not.toBeInTheDocument();
});
