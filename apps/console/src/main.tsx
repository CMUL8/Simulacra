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

function ClerkBridge({ children, onGiveUp }: { children: ReactNode; onGiveUp: () => void }) {
  const { isLoaded, isSignedIn, getToken, signOut } = useClerkAuth();
  const { user } = useUser();
  const [ready, setReady] = useState(false);

  // Never hang forever if Clerk JS / getToken / domain allowlist fails
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (!ready) onGiveUp();
    }, 8000);
    return () => window.clearTimeout(t);
  }, [ready, onGiveUp]);

  useEffect(() => {
    let cancelled = false;
    async function sync() {
      if (!isLoaded) return;
      if (!isSignedIn) {
        clearAuth();
        if (!cancelled) setReady(true);
        return;
      }
      try {
        const token = await Promise.race([
          getToken(),
          new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 5000)),
        ]);
        if (token) setToken(token);
        if (token) {
          const me = await Promise.race([
            fetchMe(),
            new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 5000)),
          ]);
          if (me && typeof me === "object" && "tenant_id" in me && me.tenant_id) {
            setTenantId(me.tenant_id);
          }
        }
      } catch {
        /* continue to app shell / login */
      }
      if (!cancelled) setReady(true);
    }
    void sync();
    const id = window.setInterval(async () => {
      if (!isSignedIn) return;
      try {
        const token = await getToken({ skipCache: true });
        if (token) setToken(token);
      } catch {
        /* ignore */
      }
    }, 50_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [isLoaded, isSignedIn, getToken, user?.id]);

  if (!isLoaded || !ready) {
    return (
      <div className="login-page">
        <div className="login-card">
          <p>Loading CMUL8 auth…</p>
          <p className="login-hint">If this takes more than a few seconds, Clerk may need this domain allow-listed.</p>
          <button type="button" className="ghost-btn" style={{ marginTop: 12 }} onClick={onGiveUp}>
            Continue with password login
          </button>
        </div>
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
  const [clerkFailed, setClerkFailed] = useState(false);

  useEffect(() => {
    loadAuthConfig().then(setConfig).catch(() =>
      setConfig({ clerk_enabled: false, clerk_publishable_key: null }),
    );
  }, []);

  if (!config) {
    return (
      <div className="login-page">
        <div className="login-card">Loading…</div>
      </div>
    );
  }

  const pk = config.clerk_publishable_key;
  const useClerk = Boolean(pk) && config.clerk_enabled && !clerkFailed;

  if (useClerk && pk) {
    return (
      <ClerkProvider publishableKey={pk} afterSignOutUrl="/">
        <ClerkBridge onGiveUp={() => setClerkFailed(true)}>
          <App clerkEnabled />
        </ClerkBridge>
      </ClerkProvider>
    );
  }

  return <App clerkEnabled={false} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
