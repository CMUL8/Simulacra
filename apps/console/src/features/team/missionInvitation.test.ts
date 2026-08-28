import { afterEach, expect, test, vi } from "vitest";

import {
  pendingMissionInvitation,
  rememberMissionInvitationFromLocation,
  synchronizeManagedMissionSession,
} from "./missionInvitation";

afterEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

test("managed_sign_in_recovers_pending_mission_invitation_before_session_load", async () => {
  window.history.replaceState(
    null,
    "",
    "/?mission_id=mission_close&invitation_id=invite_priya&invite_token=one-time-secret",
  );
  expect(rememberMissionInvitationFromLocation()).toBe(true);

  // The provider redirect removes query parameters, so recovery must use only
  // this tab's bounded session state.
  window.history.replaceState(null, "", "/");
  const order: string[] = [];
  const acceptInvitation = vi.fn(async () => {
    order.push("accept");
    return {};
  });
  const loadSession = vi.fn(async () => {
    order.push("session");
    return { tenant_id: "tenant_studio" };
  });

  await synchronizeManagedMissionSession("verified-provider-token", {
    setVerifiedToken: (token) => order.push(`token:${token}`),
    acceptInvitation,
    loadSession,
  });

  expect(order).toEqual(["token:verified-provider-token", "accept", "session"]);
  expect(acceptInvitation).toHaveBeenCalledWith(
    "mission_close",
    "invite_priya",
    expect.objectContaining({ token: "one-time-secret" }),
  );
  expect(pendingMissionInvitation()).toBeNull();
});

test("managed invitation acceptance failure remains retryable and blocks session loading", async () => {
  window.history.replaceState(
    null,
    "",
    "/?mission_id=mission_close&invitation_id=invite_priya&invite_token=one-time-secret",
  );
  rememberMissionInvitationFromLocation();
  const loadSession = vi.fn();

  await expect(synchronizeManagedMissionSession("verified-provider-token", {
    setVerifiedToken: vi.fn(),
    acceptInvitation: vi.fn().mockRejectedValue(new Error("unavailable")),
    loadSession,
  })).rejects.toThrow("unavailable");

  expect(loadSession).not.toHaveBeenCalled();
  expect(pendingMissionInvitation()).toMatchObject({
    missionId: "mission_close",
    invitationId: "invite_priya",
    token: "one-time-secret",
  });
});
