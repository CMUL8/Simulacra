import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../../../api";
import { ProjectRoomContainer } from "../../project-room/ProjectRoomContainer";
import { TeamRoster } from "../../team/TeamRoster";
import { CrewRail } from "./CrewRail";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("crew_rail_separates_agents_and_humans_and_renders_server_status", () => {
  render(
    <CrewRail
      agents={[{ id: "agent_research", name: "Researcher", role: "Evidence analyst", status: "working" }]}
      humans={[
        { id: "human_ada", name: "Ada", role: "owner", status: "online" },
        { id: "human_priya", name: "Priya", role: "reviewer", status: "away" },
      ]}
      canAddAgent
      canInviteHuman
      onAddAgent={vi.fn()}
      onInviteHuman={vi.fn()}
    />,
  );

  expect(screen.getByRole("heading", { name: "Agents" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Humans" })).toBeInTheDocument();
  expect(screen.getByText("Researcher")).toBeInTheDocument();
  expect(screen.getByText("Ada")).toBeInTheDocument();
  expect(screen.getByText("Online")).toBeInTheDocument();
  expect(screen.getByText("Away")).toBeInTheDocument();
  expect(screen.queryByText(/runtime|provider|model|computer|host|MCP/i)).not.toBeInTheDocument();
});

test("presence_heartbeat_stops_when_hidden_and_roster_renders_server_status", async () => {
  const payload: api.Cmul8RoomPayload = {
    room: {
      id: "room_close",
      revision: 1,
      members: [
        { actor_id: "human_ada", display_name: "Ada", role: "owner", actor_type: "human" },
        { actor_id: "human_priya", display_name: "Priya", role: "reviewer", actor_type: "human" },
      ],
    },
    project: { id: "mission_close", name: "August close", objective: "Verify the monthly close" },
    tasks: [], comments: [], reviews: [], events: [], mission_plan: null,
    away: { total: 0, unread: 0, counts: {}, highlights: [] },
    permissions: { manage_tasks: true, review_tasks: true, review_graph: true, invite: true, comment: true },
    presence: [
      { actor_id: "human_ada", status: "online", last_seen_at: "2026-08-28T10:00:00Z" },
      { actor_id: "human_priya", status: "away", last_seen_at: "2026-08-28T09:58:00Z" },
    ],
  };
  const getRoom = vi.spyOn(api, "getCmul8Room").mockResolvedValue(payload);
  const heartbeat = vi.spyOn(api, "heartbeatCmul8Presence").mockResolvedValue({ presence: payload.presence[0] });
  let visibility: DocumentVisibilityState = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  const intervals = new Map<number, { handler: TimerHandler; timeout: number }>();
  let nextTimer = 40;
  vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler, timeout?: number) => {
    nextTimer += 1;
    intervals.set(nextTimer, { handler, timeout: timeout ?? 0 });
    return nextTimer;
  });
  const cleared = new Set<number>();
  vi.spyOn(window, "clearInterval").mockImplementation((id?: number) => {
    if (id !== undefined) cleared.add(id);
  });

  render(<ProjectRoomContainer projectId="mission_close" mission={{
    mission: { title: "August close" },
    agents: [{ id: "agent_analyst", name: "Analyst", role: "Reconciliation analyst", mandate: "Reconcile the close", scope: "documents", autonomy: "assist" }],
    runs: [], triggers: [], deliverables: [], events: [], approvals: [],
    readiness: { graph: { status: "approved", revision: 1 }, crew_count: 1 },
  }} />);
  await waitFor(() => expect(getRoom).toHaveBeenCalled());
  await waitFor(() => expect(heartbeat).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("Humans in this Mission · 2")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Agents" })).toBeInTheDocument();
  expect(screen.getByText("Analyst")).toBeInTheDocument();
  expect(screen.getByText("Online")).toBeInTheDocument();
  expect(screen.getByText("Away")).toBeInTheDocument();

  const refreshTimer = [...intervals.entries()].find(([, item]) => item.timeout === 5_000);
  const presenceTimer = [...intervals.entries()].find(([, item]) => item.timeout === 30_000);
  expect(refreshTimer).toBeDefined();
  expect(presenceTimer).toBeDefined();

  visibility = "hidden";
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(cleared).toContain(refreshTimer![0]);
  expect(cleared).toContain(presenceTimer![0]);

  const heartbeatCount = heartbeat.mock.calls.length;
  await act(async () => {
    const handler = presenceTimer![1].handler;
    if (typeof handler === "function") await handler();
  });
  expect(heartbeat).toHaveBeenCalledTimes(heartbeatCount);
});

test("Mission owner creates a scoped human invitation and receives the secure join link", async () => {
  const invite = vi.fn().mockResolvedValue({
    url: "https://missions.example/?mission_id=mission_close&invitation_id=invite_priya&invite_token=one-time-token",
    expiresAt: "2026-09-04T10:00:00Z",
  });
  render(<TeamRoster members={[{ id: "human_ada", name: "Ada", role: "owner", kind: "human", presence: "active" }]} canInvite onInviteMember={invite} />);

  fireEvent.click(screen.getByRole("button", { name: "Invite human" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Email" }), { target: { value: "priya@example.test" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Role" }), { target: { value: "reviewer" } });
  fireEvent.click(screen.getByRole("button", { name: "Create invitation" }));

  await waitFor(() => expect(invite).toHaveBeenCalledWith("priya@example.test", "reviewer"));
  const invitationLink = await screen.findByRole("textbox", { name: "Invitation link" });
  expect((invitationLink as HTMLInputElement).value).toContain("invitation_id=invite_priya");
  expect(screen.getByText("They will join this Mission only.")).toBeInTheDocument();
});
