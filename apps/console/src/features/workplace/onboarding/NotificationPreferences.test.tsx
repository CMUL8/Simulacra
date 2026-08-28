import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { NotificationPreference } from "../../../api";
import { NotificationPreferences } from "./NotificationPreferences";

afterEach(cleanup);

const preference: NotificationPreference = {
  event_selection: "all_actionable",
  channels: ["browser"],
  digest: "off",
  muted_mission_ids: [],
  revision: 3,
  updated_at: "2026-08-28T10:00:00Z",
};

test("notification_preference_event_selection_and_mute_preserve_attention", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <NotificationPreferences
      preference={preference}
      missions={[{ id: "mission_close", title: "August close" }]}
      actionableCount={4}
      onSave={onSave}
    />,
  );

  expect(screen.getByText(/4 items still need you in Missions/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Notify me about" }), {
    target: { value: "mentions_and_decisions" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "Mute external notifications for August close" }));
  fireEvent.click(screen.getByRole("button", { name: "Save notification preferences" }));

  await waitFor(() => expect(onSave).toHaveBeenCalledWith({
    expected_revision: 3,
    event_selection: "mentions_and_decisions",
    channels: ["browser"],
    digest: "off",
    muted_mission_ids: ["mission_close"],
  }));
  expect(screen.getByText(/Needs you remains available/i)).toBeInTheDocument();
});
