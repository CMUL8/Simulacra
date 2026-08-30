import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import {
  openWorkspaceEventStream,
  ApiError,
  setTenantId,
  setToken,
  type ConversationMessage,
  type WorkspaceEventStream,
} from "../../../api";
import { useMissionConversationLive } from "../shell/useWorkplaceQuery";
import { ConversationTimeline } from "./ConversationTimeline";

function message(id: string, body: string, at: string, name: string): ConversationMessage {
  return {
    id,
    mission_id: "mission_close",
    kind: "human_message",
    author: { id: `author_${id}`, kind: "human", display_name: name, avatar_url: null },
    body,
    created_at: at,
    edited_at: null,
    thread: { reply_count: 0, latest_replies: [] },
    reactions: [],
    saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
}

afterEach(() => {
  cleanup();
  setToken(null);
  setTenantId("");
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test("durable conversation renders oldest to newest with stable day separators", () => {
  render(<ConversationTimeline
    messages={[
      message("message_1", "First update", "2026-01-02T09:00:00Z", "Ada"),
      message("message_2", "Second update", "2026-01-02T10:00:00Z", "Analyst"),
      message("message_3", "Third update", "2026-01-03T10:00:00Z", "Maya"),
    ]}
    loading={false}
    error={null}
    hasOlder={false}
    loadingOlder={false}
    onLoadOlder={() => undefined}
  />);
  const rows = screen.getAllByRole("article");
  expect(rows.map((row) => row.textContent)).toEqual([
    expect.stringContaining("First update"),
    expect.stringContaining("Second update"),
    expect.stringContaining("Third update"),
  ]);
  expect(document.querySelectorAll(".conversation-day")).toHaveLength(2);
  expect(document.querySelector(".conversation-timeline-content")).not.toBeNull();
  expect(document.body).not.toHaveTextContent("author_message_1");
});

test("earlier history has one explicit bounded load action", () => {
  const onLoadOlder = vi.fn();
  const view = render(<ConversationTimeline messages={[]} loading={false} error={null} hasOlder loadingOlder={false} onLoadOlder={onLoadOlder} />);
  fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }));
  expect(onLoadOlder).toHaveBeenCalledTimes(1);
  view.rerender(<ConversationTimeline messages={[]} loading={false} error={null} hasOlder loadingOlder onLoadOlder={onLoadOlder} />);
  expect(screen.getByRole("button", { name: "Loading earlier messages…" })).toBeDisabled();
});

test("an appended durable message waits behind a keyboard accessible new-messages control when reading history", () => {
  const first = message("message_1", "First update", "2026-01-02T09:00:00Z", "Ada");
  const second = message("message_2", "New update", "2026-01-02T10:00:00Z", "Analyst");
  const props = {
    loading: false,
    error: null,
    hasOlder: false,
    loadingOlder: false,
    onLoadOlder: () => undefined,
  };
  const view = render(<ConversationTimeline {...props} messages={[first]} />);
  const timeline = screen.getByRole("region", { name: "Mission conversation" });
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, writable: true, value: 120 },
  });
  fireEvent.scroll(timeline);
  view.rerender(<ConversationTimeline {...props} messages={[first, second]} />);

  const newMessages = screen.getByRole("button", { name: "1 new message" });
  expect(timeline.scrollTop).toBe(120);
  newMessages.focus();
  expect(newMessages).toHaveFocus();
  fireEvent.click(newMessages);
  expect(timeline.scrollTop).toBe(1000);
  expect(screen.queryByRole("button", { name: /new message/ })).not.toBeInTheDocument();
  const newestMessage = screen.getAllByRole("article")[1];
  expect(newestMessage).toHaveFocus();
  expect(newestMessage).toHaveAccessibleName("Newest message from Analyst");
});

test("authoritative removal preserves the reader's position in loaded history", () => {
  const first = message("message_removed", "Removed update", "2026-01-02T09:00:00Z", "Ada");
  const second = message("message_kept", "Kept update", "2026-01-02T10:00:00Z", "Analyst");
  const props = { loading: false, error: null, hasOlder: false, loadingOlder: false, onLoadOlder: () => undefined };
  const view = render(<ConversationTimeline {...props} messages={[first, second]} />);
  const timeline = screen.getByRole("region", { name: "Mission conversation" });
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1_000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, writable: true, value: 300 },
  });
  fireEvent.scroll(timeline);
  view.rerender(<ConversationTimeline {...props} messages={[{ ...first }, { ...second }]} />);
  Object.defineProperty(timeline, "scrollHeight", { configurable: true, value: 800 });
  view.rerender(<ConversationTimeline {...props} messages={[{ ...second }]} />);

  expect(timeline.scrollTop).toBe(100);
  expect(screen.getByText("Kept update")).toBeInTheDocument();
});

test("authoritative reconciliation preserves the first visible middle message and pixel offset", () => {
  const first = message("message_above", "Above viewport", "2026-01-02T08:00:00Z", "Ada");
  const middle = message("message_anchor", "Reading this", "2026-01-02T09:00:00Z", "Maya");
  const last = message("message_below", "Below viewport", "2026-01-02T10:00:00Z", "Analyst");
  const props = { loading: false, error: null, hasOlder: false, loadingOlder: false, onLoadOlder: () => undefined };
  const view = render(<ConversationTimeline {...props} messages={[first, middle, last]} />);
  const timeline = screen.getByRole("region", { name: "Mission conversation" });
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1_000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, writable: true, value: 300 },
    getBoundingClientRect: { configurable: true, value: () => ({ top: 0, bottom: 200, left: 0, right: 500, width: 500, height: 200, x: 0, y: 0, toJSON: () => ({}) }) },
  });
  const articles = screen.getAllByRole("article");
  let middleTop = 20;
  Object.defineProperty(articles[0], "getBoundingClientRect", { configurable: true, value: () => ({ top: -120, bottom: -20 }) });
  Object.defineProperty(articles[1], "getBoundingClientRect", { configurable: true, value: () => ({ top: middleTop, bottom: middleTop + 80 }) });
  Object.defineProperty(articles[2], "getBoundingClientRect", { configurable: true, value: () => ({ top: middleTop + 80, bottom: middleTop + 160 }) });
  fireEvent.scroll(timeline);

  middleTop = 55;
  view.rerender(<ConversationTimeline {...props} messages={[{ ...middle }, { ...last }]} />);

  expect(timeline.scrollTop).toBe(335);
  expect(screen.getByText("Reading this")).toBeInTheDocument();
});

test("substantive messages expose concise keyboard actions without exposing identity ids", () => {
  const onReply = vi.fn();
  const onToggleReaction = vi.fn();
  const onToggleSaved = vi.fn();
  const acknowledged = {
    ...message("message_actions", "Review is ready", "2026-01-02T10:00:00Z", "Analyst"),
    reactions: [{ reaction: "check" as const, count: 2, reacted: true }],
    saved: true,
  };
  render(<ConversationTimeline
    messages={[acknowledged]}
    loading={false}
    error={null}
    hasOlder={false}
    loadingOlder={false}
    onLoadOlder={() => undefined}
    onReply={onReply}
    onToggleReaction={onToggleReaction}
    onToggleSaved={onToggleSaved}
  />);

  const reply = screen.getByRole("button", { name: "Reply to Analyst" });
  reply.focus();
  expect(reply).toHaveFocus();
  fireEvent.click(reply);
  fireEvent.click(screen.getByRole("button", { name: "Remove acknowledgement · 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Unsave message" }));

  expect(onReply).toHaveBeenCalledWith(acknowledged, reply);
  expect(onToggleReaction).toHaveBeenCalledWith(acknowledged, "check", false);
  expect(onToggleSaved).toHaveBeenCalledWith(acknowledged, false);
  expect(document.body).not.toHaveTextContent("✓");
  expect(document.body).not.toHaveTextContent("author_message_actions");
});

test("an agent completion opens its exact reviewable Work item without exposing durable ids", () => {
  const onOpenWork = vi.fn();
  const completion = {
    ...message("message_completion", "The report is ready for review.", "2026-01-02T10:00:00Z", "Fin"),
    kind: "agent_completed",
    author: { id: "agent_fin", kind: "agent" as const, display_name: "Fin", avatar_url: null },
    links: { work_item_id: "task_completion", run_id: "run_completion", output_id: "output_completion" },
  };
  render(<ConversationTimeline
    messages={[completion]}
    loading={false}
    error={null}
    hasOlder={false}
    loadingOlder={false}
    onLoadOlder={() => undefined}
    onOpenWork={onOpenWork}
  />);

  const completionRow = screen.getByRole("article").closest(".conversation-message-wrap");
  expect(completionRow).toHaveClass("is-work-event");
  const review = screen.getByRole("button", { name: "Review output" });
  expect(review).toHaveClass("is-primary-action");
  fireEvent.click(review);

  expect(onOpenWork).toHaveBeenCalledWith("task_completion", "verify_output");
  expect(document.body).not.toHaveTextContent("task_completion");
  expect(document.body).not.toHaveTextContent("run_completion");
  expect(document.body).not.toHaveTextContent("output_completion");
});

test("agent progress is a compact durable status instead of exposed reasoning", () => {
  const progress = {
    ...message("message_progress", "Working on the assignment. Progress and questions will return here.", "2026-01-02T09:30:00Z", "Fin"),
    kind: "agent_started",
    author: { id: "agent_fin", kind: "agent" as const, display_name: "Fin", avatar_url: null },
    links: { work_item_id: "task_progress", run_id: "run_progress", output_id: null },
  };
  render(<ConversationTimeline
    messages={[progress]}
    loading={false}
    error={null}
    hasOlder={false}
    loadingOlder={false}
    onLoadOlder={() => undefined}
  />);

  const row = screen.getByRole("article");
  expect(row).toHaveClass("is-progress");
  expect(row.closest(".conversation-message-wrap")).toHaveClass("is-work-event");
  expect(row).toHaveTextContent("Work started");
  expect(row).toHaveTextContent("Working on the assignment");
  expect(row).not.toHaveTextContent("task_progress");
  expect(row).not.toHaveTextContent("run_progress");
  expect(screen.queryByRole("button", { name: "Reply to Fin" })).not.toBeInTheDocument();
});

test("message overflow copies content and a stable Mission link", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const item = message("message_copy", "Copy this update", "2026-01-02T10:00:00Z", "Ada");
  render(<ConversationTimeline messages={[item]} loading={false} error={null} hasOlder={false} loadingOlder={false} onLoadOlder={() => undefined} />);
  fireEvent.click(screen.getByText("More"));
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
  expect(writeText).toHaveBeenNthCalledWith(1, "Copy this update");
  expect(writeText).toHaveBeenNthCalledWith(2, `${window.location.origin}/missions/mission_close/conversation?focus=message_copy`);
});

type LiveHarnessProps = {
  stream: WorkspaceEventStream;
  fetchNewest: () => Promise<{ items: ConversationMessage[] }>;
  missionId?: string;
  enabled?: boolean;
  onAccessLost?: () => void;
};

function LiveHarness({ stream, fetchNewest, missionId = "mission_close", enabled = true, onAccessLost }: LiveHarnessProps) {
  const [messages, setMessages] = useState([
    message("message_live", "Original durable text", "2026-01-02T09:00:00Z", "Ada"),
  ]);
  useMissionConversationLive({
    enabled,
    missionId,
    stream,
    pollIntervalMs: 1_000,
    onAccessLost,
    onRefresh: async () => {
      const page = await fetchNewest();
      setMessages((current) => {
        const byId = new Map(current.map((item) => [item.id, item]));
        page.items.forEach((item) => byId.set(item.id, item));
        return [...byId.values()].sort((left, right) => left.created_at.localeCompare(right.created_at));
      });
    },
  });
  return <ConversationTimeline
    messages={messages}
    loading={false}
    error={null}
    hasOlder={false}
    loadingOlder={false}
    onLoadOlder={() => undefined}
  />;
}

test("live stream cancels on Mission change and stays off behind its release flag", async () => {
  const signals: AbortSignal[] = [];
  const stream: WorkspaceEventStream = async (options) => {
    signals.push(options.signal);
    options.onOpen();
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const fetchNewest = vi.fn().mockResolvedValue({ items: [] });
  const view = render(<LiveHarness stream={stream} fetchNewest={fetchNewest} enabled={false} />);
  expect(signals).toHaveLength(0);
  view.rerender(<LiveHarness stream={stream} fetchNewest={fetchNewest} missionId="mission_one" />);
  await waitFor(() => expect(signals).toHaveLength(1));
  view.rerender(<LiveHarness stream={stream} fetchNewest={fetchNewest} missionId="mission_two" />);
  await waitFor(() => expect(signals).toHaveLength(2));
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
});

test("reconnect_fetches_durable_page_without_duplicate_timeline_rows", async () => {
  const live: { wakeUp?: (event: { id: string; type: string; mission_id: string; occurred_at: string }) => void } = {};
  const stream: WorkspaceEventStream = async (options) => {
    live.wakeUp = options.onWakeUp;
    options.onOpen();
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const changed = {
    ...message("message_live", "Durable text after reconnect", "2026-01-02T09:00:00Z", "Ada"),
    thread: { reply_count: 1, latest_replies: [message("reply_1", "Thread reply", "2026-01-02T09:30:00Z", "Maya")] },
  };
  const added = message("message_added", "New durable row", "2026-01-02T10:00:00Z", "Analyst");
  const fetchNewest = vi.fn().mockResolvedValue({ items: [changed, added] });
  render(<LiveHarness stream={stream} fetchNewest={fetchNewest} />);
  await waitFor(() => expect(live.wakeUp).toBeTypeOf("function"));

  const timeline = screen.getByRole("region", { name: "Mission conversation" });
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1_000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, writable: true, value: 120 },
  });
  fireEvent.scroll(timeline);

  live.wakeUp?.({ id: "event_1", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-02T10:00:00Z" });
  await screen.findByText("Durable text after reconnect");
  expect(screen.getAllByRole("article")).toHaveLength(2);
  expect(timeline.scrollTop).toBe(120);
  expect(screen.getByRole("button", { name: "1 new message" })).toBeInTheDocument();

  live.wakeUp?.({ id: "event_1", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-02T10:00:00Z" });
  await waitFor(() => expect(fetchNewest).toHaveBeenCalledTimes(1));
  expect(screen.getAllByText("New durable row")).toHaveLength(1);
});

test("live refresh starts bounded polling after two failures and cancels both paths on cleanup", async () => {
  vi.useFakeTimers();
  const aborts: AbortSignal[] = [];
  const recovered: { wakeUp?: Parameters<WorkspaceEventStream>[0]["onWakeUp"] } = {};
  const stream = vi.fn<WorkspaceEventStream>(async (options) => {
    aborts.push(options.signal);
    options.onOpen();
    if (aborts.length < 3) throw new Error("disconnected");
    recovered.wakeUp = options.onWakeUp;
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  });
  const fetchNewest = vi.fn().mockResolvedValue({ items: [] });
  const view = render(<LiveHarness stream={stream} fetchNewest={fetchNewest} />);

  await vi.advanceTimersByTimeAsync(3_100);
  expect(stream.mock.calls.length).toBeGreaterThanOrEqual(2);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(fetchNewest).toHaveBeenCalled();
  const beforeRecovery = fetchNewest.mock.calls.length;
  recovered.wakeUp?.({ id: "event_recovered", type: "conversation_changed", mission_id: "mission_close", occurred_at: "2026-01-02T10:00:00Z" });
  await vi.advanceTimersByTimeAsync(4_000);
  expect(fetchNewest).toHaveBeenCalledTimes(beforeRecovery + 1);
  view.unmount();
  expect(aborts.every((signal) => signal.aborted)).toBe(true);
  const calls = fetchNewest.mock.calls.length;
  await vi.advanceTimersByTimeAsync(5_000);
  expect(fetchNewest).toHaveBeenCalledTimes(calls);
  vi.useRealTimers();
});

test("workspace cursor advances across other Missions and reset still reconciles the selected Mission", async () => {
  vi.useFakeTimers();
  const calls: Array<Parameters<WorkspaceEventStream>[0]> = [];
  const stream: WorkspaceEventStream = async (options) => {
    calls.push(options);
    options.onOpen();
    if (calls.length === 1) {
      options.onWakeUp({ id: "event_other", type: "conversation_changed", mission_id: "mission_other", occurred_at: "2026-01-02T10:00:00Z" });
      throw new Error("reconnect");
    }
    await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
  };
  const fetchNewest = vi.fn().mockResolvedValue({ items: [] });
  render(<LiveHarness stream={stream} fetchNewest={fetchNewest} />);
  await vi.advanceTimersByTimeAsync(1_100);
  expect(calls[1]?.lastEventId).toBe("event_other");
  expect(fetchNewest).not.toHaveBeenCalled();
  calls[1]?.onWakeUp({ id: "event_reset", type: "workspace.reset", mission_id: "workspace", occurred_at: "2026-01-02T10:01:00Z" });
  await vi.runAllTicks();
  expect(fetchNewest).toHaveBeenCalledTimes(1);
});

test("authorization loss stops reconnect and polling", async () => {
  vi.useFakeTimers();
  const stream = vi.fn<WorkspaceEventStream>(async () => {
    throw new ApiError(403, "forbidden");
  });
  const fetchNewest = vi.fn().mockResolvedValue({ items: [] });
  const onAccessLost = vi.fn();
  render(<LiveHarness stream={stream} fetchNewest={fetchNewest} onAccessLost={onAccessLost} />);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(stream).toHaveBeenCalledTimes(1);
  expect(fetchNewest).not.toHaveBeenCalled();
  expect(onAccessLost).toHaveBeenCalledTimes(1);
});

test("authenticated live stream keeps credentials out of the URL and resumes with Last-Event-ID", async () => {
  setToken("secret-session-token");
  setTenantId("tenant_private");
  const wakeUp = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(": keepalive\r\n\r\ndata: not-json\r\n\r\nid: event_9\r\ndata: {\"id\":\"event_9\",\"type\":\"conversation_changed\","));
      controller.enqueue(new TextEncoder().encode("\"mission_id\":\"mission_close\",\"occurred_at\":\"2026-01-02T10:00:00Z\"}\r\n\r\n"));
      controller.close();
    },
  });
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));

  await expect(openWorkspaceEventStream({
    lastEventId: "event_8",
    signal: new AbortController().signal,
    onOpen: vi.fn(),
    onWakeUp: wakeUp,
  })).rejects.toThrow("Live updates are temporarily unavailable");

  const [url, init] = fetcher.mock.calls[0];
  expect(String(url)).toMatch(/\/workspace\/events$/);
  expect(String(url)).not.toContain("secret-session-token");
  expect(String(url)).not.toContain("tenant_private");
  const headers = new Headers(init?.headers);
  expect(headers.get("Authorization")).toBe("Bearer secret-session-token");
  expect(headers.get("X-Tenant-Id")).toBe("tenant_private");
  expect(headers.get("Last-Event-ID")).toBe("event_8");
  expect(wakeUp).toHaveBeenCalledWith(expect.objectContaining({ id: "event_9", mission_id: "mission_close" }));
});
