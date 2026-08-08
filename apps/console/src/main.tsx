import { ClerkProvider, useAuth as useClerkAuth, useUser } from "@clerk/clerk-react";
import { StrictMode, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { clearAuth, fetchMe, setTenantId, setToken } from "./api";
import "./styles.css";

type AuthConfig = {
  clerk_enabled: boolean;
  clerk_publishable_key: string | null;
};

async function loadAuthConfig(): Promise<AuthConfig> {
  const base = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "" : "/api");
  try {
    const res = await fetch(`${base}/auth/config`);
    if (res.ok) return res.json();
  } catch {
    /* fall through */
  }
  const baked = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined) || "";
  return {
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

  useEffect(() => {
    let cancelled = false;
    async function sync() {
      if (!isLoaded) return;
      if (!isSignedIn) {
        clearAuth();
        return;
      }
      try {
        const token = await Promise.race([
          getToken(),
          new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 4000)),
        ]);
        if (cancelled || !token) return;
        setToken(token);
        const me = await fetchMe().catch(() => null);
        if (me?.tenant_id) setTenantId(me.tenant_id);
        // Force a reload so App picks up the session token
        window.location.hash = "";
        window.location.reload();
      } catch {
        /* stay on login */
      }
    }
    void sync();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, getToken, user?.id]);

  return (
    <>
      {children}
      <ClerkSignOutBridge signOut={signOut} />
    </>
  );
}

function Root() {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [wantClerk, setWantClerk] = useState(false);

  useEffect(() => {
    loadAuthConfig()
      .then(setConfig)
      .catch(() => setConfig({ clerk_enabled: false, clerk_publishable_key: null }));
  }, []);

  if (!config) {
    return (
      <div className="login-page">
        <div className="login-card">Loading…</div>
      </div>
    );
  }

  const pk = config.clerk_publishable_key;
  const clerkAvailable = Boolean(pk) && config.clerk_enabled;

  // Default path: password login immediately (no Clerk hang)
  if (!wantClerk || !pk || !clerkAvailable) {
    return (
      <App
        clerkEnabled={false}
        clerkAvailable={clerkAvailable}
        onUseClerk={() => setWantClerk(true)}
      />
    );
  }

  return (
    <ClerkProvider publishableKey={pk} afterSignOutUrl="/">
      <ClerkSessionSync>
        <App clerkEnabled clerkAvailable onUseClerk={() => setWantClerk(true)} />
      </ClerkSessionSync>
    </ClerkProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
