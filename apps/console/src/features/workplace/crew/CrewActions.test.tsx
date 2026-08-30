import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../../../api";
import { CrewActions } from "./CrewActions";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("Mission crew actions add a specialist without infrastructure choices", async () => {
  const create = vi.spyOn(api, "createMissionAgent").mockResolvedValue({});
  const onAgentAdded = vi.fn();
  render(<CrewActions missionId="mission_close" canAddAgent canInviteHuman onAgentAdded={onAgentAdded} />);

  expect(screen.queryByRole("button", { name: "Add agent" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Invite human" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add to crew" }));
  fireEvent.click(screen.getByRole("button", { name: "Add agent" }));
  const dialog = screen.getByRole("dialog", { name: "Add an agent" });
  expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue("");
  expect(within(dialog).getByPlaceholderText("e.g. Operations analyst")).toHaveValue("");
  expect(dialog).not.toHaveTextContent(/runtime|provider|model|computer|host|MCP/i);

  fireEvent.click(within(dialog).getByRole("button", { name: "Research" }));
  expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue("Research analyst");
  fireEvent.click(within(dialog).getByRole("button", { name: "Add agent" }));

  await waitFor(() => expect(create).toHaveBeenCalledWith("mission_close", expect.objectContaining({ name: "Research analyst", scope: "sources" })));
  expect(onAgentAdded).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog", { name: "Add an agent" })).not.toBeInTheDocument();
});

test("Mission crew action creates a scoped human invitation", async () => {
  vi.spyOn(api, "createCmul8Invitation").mockResolvedValue({
    invitation: { id: "invite_priya", status: "pending", revision: 0, expires_at: "2026-09-05T10:00:00Z" },
    token: "secure-token",
  });
  render(<CrewActions missionId="mission_close" canAddAgent canInviteHuman onAgentAdded={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "Add to crew" }));
  fireEvent.click(screen.getByRole("button", { name: "Invite human" }));
  const dialog = screen.getByRole("dialog", { name: "Invite a human" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Email" }), { target: { value: "priya@example.test" } });
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Mission role" }), { target: { value: "reviewer" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create invitation" }));

  const link = await within(dialog).findByRole("textbox", { name: "Invitation link" });
  expect((link as HTMLInputElement).value).toContain("mission_id=mission_close");
  expect((link as HTMLInputElement).value).toContain("invite_token=secure-token");
});
