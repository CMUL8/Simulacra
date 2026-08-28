import { ClerkProvider, useAuth as useClerkAuth, useUser } from "@clerk/clerk-react";
import { StrictMode, useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { MissionLoader } from "./components/MissionLoader";
import { acceptCmul8Invitation, clearAuth, fetchMe, setTenantId, setToken } from "./api";
import {
  pendingMissionInvitation,
  rememberMissionInvitationFromLocation,
  synchronizeManagedMissionSession,
} from "./features/team/missionInvitation";
import "generative-loaders/styles.css";
import "./styles.css";

type AuthConfig = {
  auth_required: boolean;
  clerk_enabled: boolean;
  clerk_publishable_key: string | null;
};

async function loadAuthConfig(): Promise<AuthConfig> {
  const base = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "" : "/api");
  try {
    const res = await fetch(`${base}/auth/config`, {
      signal: AbortSignal.timeout(2500),
    });
    if (res.ok) return res.json();
  } catch {
    /* fall through — local Vite without API should still show the landing */
  }
  const baked = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined) || "";
  return {
    auth_required: true,
    clerk_enabled: Boolean(baked),
    clerk_publishable_key: baked || null,
  };
}

function ClerkSignOutBridge({ signOut }: { signOut: () => Promise<void> }) {
  useEffect(() => {
    (window as unknown as { __simulacraClerkSignOut?: () => Promise<void> }).__simulacraClerkSignOut =
      signOut;
  }, [signOut]);
  return null;
}

/** Only mounts when user opted into Clerk — never blocks the password login path. */
function ClerkSessionSync({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, getToken, signOut } = useClerkAuth();
  const { user } = useUser();
  const [syncState, setSyncState] = useState<"checking" | "ready" | "failed">("checking");
  const [attempt, setAttempt] = useState(0);
  const startedFor = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function sync() {
      if (!isLoaded) return;
      if (!isSignedIn) {
        clearAuth();
        setSyncState("ready");
        return;
      }
      const attemptKey = `${user?.id ?? "signed-in"}:${attempt}`;
      if (startedFor.current === attemptKey) return;
      startedFor.current = attemptKey;
      setSyncState("checking");
      try {
        const token = await Promise.race([
          getToken(),
          new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 4000)),
        ]);
        if (cancelled) return;
        if (!token) throw new Error("managed sign-in unavailable");
        const me = await synchronizeManagedMissionSession(token, {
          setVerifiedToken: setToken,
          acceptInvitation: acceptCmul8Invitation,
          loadSession: fetchMe,
        });
        if (cancelled) return;
        if (me?.tenant_id) setTenantId(me.tenant_id);
        // Force a reload so App picks up the session token
        window.location.hash = "";
        window.location.reload();
      } catch {
        clearAuth();
        if (!cancelled) setSyncState("failed");
      }
    }
    void sync();
    return () => {
      cancelled = true;
    };
  }, [attempt, isLoaded, isSignedIn, getToken, user?.id]);

  if (!isLoaded || (isSignedIn && syncState !== "ready")) {
    return (
      <div className="landing landing-boot">
        <div className="landing-content">
          <h1 className="boot-mark">Missions</h1>
          {syncState === "failed" ? (
            <>
              <p>We couldn’t finish joining this Mission.</p>
              <button type="button" className="approve-btn" onClick={() => setAttempt((value) => value + 1)}>
                Try again
              </button>
            </>
          ) : (
            <MissionLoader label="Joining Mission" variant="matrix" className="landing-boot-status" />
          )}
        </div>
        <ClerkSignOutBridge signOut={signOut} />
      </div>
    );
  }

  return (
    <>
      {children}
      <ClerkSignOutBridge signOut={signOut} />
    </>
  );
}

function Root() {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [wantClerk, setWantClerk] = useState(() => pendingMissionInvitation() !== null);

  useEffect(() => {
    loadAuthConfig()
      .then(setConfig)
      .catch(() => setConfig({ auth_required: true, clerk_enabled: false, clerk_publishable_key: null }));
  }, []);

  if (!config) {
    return (
      <div className="landing landing-boot">
        <div className="landing-content">
          <h1 className="boot-mark">Missions</h1>
          <MissionLoader label="Opening workspace" variant="matrix" className="landing-boot-status" />
        </div>
      </div>
    );
  }

  const pk = config.clerk_publishable_key;
  const clerkAvailable = Boolean(pk) && config.clerk_enabled;
  const beginManagedSignIn = () => {
    rememberMissionInvitationFromLocation();
    setWantClerk(true);
  };

  // Default path: password login immediately (no Clerk hang)
  if (!wantClerk || !pk || !clerkAvailable) {
    return (
      <App
        authRequired={config.auth_required}
        clerkEnabled={false}
        clerkAvailable={clerkAvailable}
        onUseClerk={beginManagedSignIn}
      />
    );
  }

  return (
    <ClerkProvider publishableKey={pk} afterSignOutUrl="/">
      <ClerkSessionSync>
        <App authRequired={config.auth_required} clerkEnabled clerkAvailable onUseClerk={beginManagedSignIn} />
      </ClerkSessionSync>
    </ClerkProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
