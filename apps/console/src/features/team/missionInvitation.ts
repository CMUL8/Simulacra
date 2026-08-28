const PENDING_MISSION_INVITATION_KEY = "missions.pending-invitation.v1";
const INVITATION_QUERY_KEYS = ["mission_id", "invitation_id", "invite_token"] as const;

export type PendingMissionInvitation = {
  missionId: string;
  invitationId: string;
  token: string;
  clientRequestId: string;
};

type ManagedSessionDependencies<T> = {
  setVerifiedToken: (token: string) => void;
  acceptInvitation: (
    missionId: string,
    invitationId: string,
    body: { client_request_id: string; token: string },
  ) => Promise<unknown>;
  loadSession: () => Promise<T>;
};

function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isPendingMissionInvitation(value: unknown): value is PendingMissionInvitation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PendingMissionInvitation>;
  return nonempty(candidate.missionId)
    && nonempty(candidate.invitationId)
    && nonempty(candidate.token)
    && nonempty(candidate.clientRequestId);
}

function newClientRequestId(): string {
  return `invite_${crypto.randomUUID().replaceAll("-", "")}`;
}

export function missionInvitationFromLocation(): PendingMissionInvitation | null {
  if (typeof window === "undefined") return null;
  const query = new URLSearchParams(window.location.search);
  const missionId = query.get("mission_id")?.trim();
  const invitationId = query.get("invitation_id")?.trim();
  const token = query.get("invite_token")?.trim();
  if (!missionId || !invitationId || !token) return null;
  return { missionId, invitationId, token, clientRequestId: newClientRequestId() };
}

export function pendingMissionInvitation(): PendingMissionInvitation | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(PENDING_MISSION_INVITATION_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isPendingMissionInvitation(parsed)) return parsed;
  } catch {
    // Invalid tab-scoped state is discarded without exposing its contents.
  }
  window.sessionStorage.removeItem(PENDING_MISSION_INVITATION_KEY);
  return null;
}

export function rememberMissionInvitationFromLocation(): boolean {
  if (typeof window === "undefined") return false;
  const invitation = missionInvitationFromLocation();
  if (!invitation) return pendingMissionInvitation() !== null;
  window.sessionStorage.setItem(PENDING_MISSION_INVITATION_KEY, JSON.stringify(invitation));
  return true;
}

export function clearMissionInvitation() {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(PENDING_MISSION_INVITATION_KEY);
  const url = new URL(window.location.href);
  INVITATION_QUERY_KEYS.forEach((key) => url.searchParams.delete(key));
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

/**
 * Completes the only safe managed sign-in order: establish the verified
 * provider credential, accept the pending Mission invitation, then load the
 * ordinary membership-bearing session.
 */
export async function synchronizeManagedMissionSession<T>(
  verifiedToken: string,
  dependencies: ManagedSessionDependencies<T>,
): Promise<T> {
  dependencies.setVerifiedToken(verifiedToken);
  const invitation = pendingMissionInvitation();
  if (invitation) {
    await dependencies.acceptInvitation(invitation.missionId, invitation.invitationId, {
      client_request_id: invitation.clientRequestId,
      token: invitation.token,
    });
    clearMissionInvitation();
  }
  return dependencies.loadSession();
}
