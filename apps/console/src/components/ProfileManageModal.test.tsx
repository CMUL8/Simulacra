import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import { ProfileManageModal } from "./ProfileManageModal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

test("sign in recovers a Mission invitation before opening the workspace", async () => {
  window.history.replaceState(null, "", "/?mission_id=mission_close&invitation_id=invite_priya&invite_token=secret-token-value-long-enough");
  const session: api.AuthSession = {
    token: "session_token", token_type: "bearer", tenant_id: "tenant_studio", tenants: [{ id: "tenant_studio", name: "Studio", status: "active" }],
    user: { id: "human_priya", email: "priya@example.test", name: "Priya" },
  };
  vi.spyOn(api, "login").mockResolvedValue(session);
  const accept = vi.spyOn(api, "acceptCmul8Invitation").mockResolvedValue({
    invitation: { id: "invite_priya", status: "accepted", revision: 2 },
    membership: { actor_id: "human_priya", role: "reviewer" },
  });
  const onAuthed = vi.fn();
  render(<ProfileManageModal open user={null} tenants={[]} initialTab="auth" onAuthed={onAuthed} onSignOut={vi.fn()} />);

  fireEvent.change(screen.getByRole("textbox", { name: "Email" }), { target: { value: "priya@example.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret-password" } });
  fireEvent.click(screen.getAllByRole("button", { name: /^Sign in$/ })[1]);

  await waitFor(() => expect(accept).toHaveBeenCalledWith("mission_close", "invite_priya", expect.objectContaining({ token: "secret-token-value-long-enough" })));
  expect(onAuthed).toHaveBeenCalledWith(session);
  expect(window.location.search).toBe("");
});

test("shows workspace names without exposing internal identifiers", () => {
  render(
    <ProfileManageModal
      open
      user={{ id: "usr_private", email: "human@example.test", name: "A Human" }}
      tenants={[{ id: "tenant_private", name: "Studio Max", status: "active" }]}
      tenantId="tenant_private"
      onAuthed={vi.fn()}
      onSignOut={vi.fn()}
    />,
  );

  expect(screen.getByText("Studio Max")).toBeInTheDocument();
  expect(screen.queryByText("tenant_private")).not.toBeInTheDocument();
  expect(screen.queryByText("usr_private")).not.toBeInTheDocument();
});

test("account renders human notification choices while Needs you stays authoritative", async () => {
  vi.spyOn(api, "getWorkspacePreferences").mockResolvedValue({
    work_view_preferences: [],
    notification_preference: {
      event_selection: "all_actionable", channels: ["browser"], digest: "off",
      muted_mission_ids: [], revision: 2, updated_at: "2026-08-28T10:00:00Z",
    },
  });
  vi.spyOn(api, "listMissionSummaries").mockResolvedValue({
    items: [{ id: "mission_close", title: "August close", outcome_summary: "Verified close", public_state: "active", updated_at: "2026-08-28T10:00:00Z", human_count: 2, agent_count: 1, active_work_count: 1, needs_human_count: 3, verified_output_count: 0, current_human_permissions: [] }],
    next_cursor: null,
  });
  vi.spyOn(api, "listWorkspaceAttention").mockResolvedValue({ items: [], next_cursor: null, unread_count: 0, actionable_count: 3 });
  render(
    <ProfileManageModal
      open
      user={{ id: "human_ada", email: "ada@example.test", name: "Ada" }}
      tenants={[{ id: "tenant_studio", name: "Studio", status: "active" }]}
      onAuthed={vi.fn()}
      onSignOut={vi.fn()}
    />,
  );

  expect(await screen.findByRole("heading", { name: "Notifications" })).toBeInTheDocument();
  expect(screen.getByText(/3 items still need you in Missions/)).toBeInTheDocument();
  await waitFor(() => expect(api.getWorkspacePreferences).toHaveBeenCalledTimes(1));
});

test("notification choices remain editable when optional Mission context is unavailable", async () => {
  vi.spyOn(api, "getWorkspacePreferences").mockResolvedValue({
    work_view_preferences: [],
    notification_preference: {
      event_selection: "mentions_and_decisions", channels: ["browser"], digest: "off",
      muted_mission_ids: [], revision: 4, updated_at: "2026-08-28T10:00:00Z",
    },
  });
  vi.spyOn(api, "listMissionSummaries").mockRejectedValue(new Error("temporarily unavailable"));
  vi.spyOn(api, "listWorkspaceAttention").mockRejectedValue(new Error("temporarily unavailable"));

  render(<ProfileManageModal open user={{ id: "human_ada", email: "ada@example.test", name: "Ada" }} tenants={[]} onAuthed={vi.fn()} onSignOut={vi.fn()} />);

  expect(await screen.findByRole("heading", { name: "Notifications" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Notify me about" })).toHaveValue("mentions_and_decisions");
});
