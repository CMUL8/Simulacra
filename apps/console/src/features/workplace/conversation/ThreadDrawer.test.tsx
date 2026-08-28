import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { ConversationMessage } from "../../../api";
import { ThreadDrawer } from "./ThreadDrawer";

function message(id: string, body: string, root = false): ConversationMessage {
  return {
    id,
    mission_id: "mission_close",
    kind: "human_message",
    author: { id: `human_${id}`, kind: "human", display_name: id === "root" ? "Ada" : "Maya", avatar_url: null },
    body,
    created_at: id === "root" ? "2026-01-02T09:00:00Z" : "2026-01-02T10:00:00Z",
    edited_at: null,
    thread: { reply_count: root ? 1 : 0, latest_replies: [] },
    reactions: [],
    saved: false,
    links: { work_item_id: null, run_id: null, output_id: null },
  };
}

afterEach(cleanup);

test("thread opens with root context, stays one level, and Escape returns focus", async () => {
  const opener = document.createElement("button");
  opener.textContent = "Open thread";
  document.body.append(opener);
  const root = { ...message("root", "Root context", true), thread: { reply_count: 1, latest_replies: [message("reply", "Direct reply")] } };
  const onClose = vi.fn();
  const view = render(<ThreadDrawer
    missionId="mission_close"
    root={root}
    returnFocus={opener}
    onClose={onClose}
    onReply={() => undefined}
    onAccessLost={() => undefined}
    loadReplies={async () => ({ items: root.thread.latest_replies, next_before: null })}
  />);

  expect(screen.getByRole("dialog", { name: "Thread" })).toBeInTheDocument();
  expect(screen.getByText("Root context")).toBeInTheDocument();
  expect(screen.getByText("Direct reply")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Reply to Maya/ })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Reply in thread" })).toHaveFocus());
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
  view.unmount();
  await waitFor(() => expect(opener).toHaveFocus());
  opener.remove();
});

test("thread keeps keyboard focus inside the drawer", async () => {
  render(<ThreadDrawer
    missionId="mission_close"
    root={message("root", "Root context", true)}
    returnFocus={null}
    onClose={() => undefined}
    onReply={() => undefined}
    onAccessLost={() => undefined}
    loadReplies={async () => ({ items: [], next_before: null })}
  />);
  const dialog = screen.getByRole("dialog", { name: "Thread" });
  const close = screen.getByRole("button", { name: "Close thread" });
  const input = screen.getByRole("textbox", { name: "Reply in thread" });
  await waitFor(() => expect(input).toHaveFocus());
  fireEvent.keyDown(dialog, { key: "Tab" });
  expect(close).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
  expect(input).toHaveFocus();
});

test("failed reply retry reuses the exact request id and payload while edit creates a fresh attempt", async () => {
  const reply = message("reply_new", "A durable reply");
  const sendReply = vi.fn()
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValueOnce({ message: reply })
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValueOnce({ message: { ...reply, id: "reply_edited", body: "Edited reply" } });
  const ids = ["request_1", "request_2"];
  const onReply = vi.fn();
  render(<ThreadDrawer
    missionId="mission_close"
    root={message("root", "Root context", true)}
    returnFocus={null}
    onClose={() => undefined}
    onReply={onReply}
    onAccessLost={() => undefined}
    sendReply={sendReply}
    loadReplies={async () => ({ items: [], next_before: null })}
    requestIdFactory={() => ids.shift() || "unexpected"}
  />);

  const input = screen.getByRole("textbox", { name: "Reply in thread" });
  fireEvent.change(input, { target: { value: "A durable reply" } });
  fireEvent.click(screen.getByRole("button", { name: "Reply" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(onReply).toHaveBeenCalledWith(reply));
  expect(sendReply.mock.calls[0]).toEqual(sendReply.mock.calls[1]);

  fireEvent.change(input, { target: { value: "Draft reply" } });
  fireEvent.click(screen.getByRole("button", { name: "Reply" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Edit reply" }));
  expect(input).toHaveValue("Draft reply");
  fireEvent.change(input, { target: { value: "Edited reply" } });
  fireEvent.click(screen.getByRole("button", { name: "Reply" }));
  await waitFor(() => expect(onReply).toHaveBeenCalledTimes(2));
  expect(sendReply.mock.calls[2][2]).toEqual({ client_request_id: "request_2", body: "Draft reply" });
  expect(sendReply.mock.calls[3][2]).toEqual({ client_request_id: "unexpected", body: "Edited reply" });
});

test("thread loads authoritative reply pages without adding replies to the main timeline", async () => {
  const latest = message("reply_latest", "Latest reply");
  const earlier = { ...message("reply_earlier", "Earlier reply"), created_at: "2026-01-02T09:30:00Z" };
  const root = { ...message("root", "Root context", true), thread: { reply_count: 2, latest_replies: [latest] } };
  const loadReplies = vi.fn()
    .mockResolvedValueOnce({ items: [latest], next_before: "before_earlier" })
    .mockResolvedValueOnce({ items: [earlier], next_before: null });
  render(<ThreadDrawer
    missionId="mission_close"
    root={root}
    returnFocus={null}
    onClose={() => undefined}
    onReply={() => undefined}
    onAccessLost={() => undefined}
    loadReplies={loadReplies}
  />);

  await screen.findByText("Latest reply");
  fireEvent.click(screen.getByRole("button", { name: "Load earlier replies" }));
  await screen.findByText("Earlier reply");
  expect(loadReplies).toHaveBeenNthCalledWith(1, "mission_close", "root");
  expect(loadReplies).toHaveBeenNthCalledWith(2, "mission_close", "root", "before_earlier");
  expect(screen.getByLabelText("Thread replies").querySelectorAll("article")).toHaveLength(2);
});
