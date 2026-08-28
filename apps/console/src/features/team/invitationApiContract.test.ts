import { afterEach, expect, test, vi } from "vitest";

import { acceptCmul8Invitation, createCmul8Invitation, setTenantId, setToken } from "../../api";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

test("Mission invitation client consumes the exact create and accept wire responses", async () => {
  setToken("session-token");
  setTenantId("tenant_studio");
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({
      invitation: { id: "invite_priya", status: "pending", expires_at: "2026-09-04T10:00:00Z", revision: 1 },
      token: "one-time-token",
    }), { status: 200, headers: { "content-type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      invitation: { id: "invite_priya", status: "accepted", revision: 2 },
      membership: { actor_id: "human_priya", role: "reviewer" },
    }), { status: 200, headers: { "content-type": "application/json" } }));

  const created = await createCmul8Invitation("mission_close", { client_request_id: "create_1", email: "priya@example.test", role: "reviewer" });
  const accepted = await acceptCmul8Invitation("mission_close", created.invitation.id, { client_request_id: "accept_1", token: created.token });

  expect(created.invitation.expires_at).toBe("2026-09-04T10:00:00Z");
  expect(accepted).toEqual({ invitation: { id: "invite_priya", status: "accepted", revision: 2 }, membership: { actor_id: "human_priya", role: "reviewer" } });
  expect(fetchMock).toHaveBeenNthCalledWith(2, expect.stringContaining("/projects/mission_close/cmul8/room/invitations/invite_priya/accept"), expect.objectContaining({ method: "POST" }));
});
