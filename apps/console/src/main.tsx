import { ClerkProvider, useAuth as useClerkAuth, useUser } from "@clerk/clerk-react";
import { StrictMode, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { clearAuth, fetchMe, setTenantId, setToken } from "./api";
import "./styles.css";

const CLERK_PK = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

function ClerkSignOutBridge({ signOut }: { signOut: () => Promise<void> }) {
  useEffect(() => {
    (window as unknown as { __simulacraClerkSignOut?: () => Promise<void> }).__simulacraClerkSignOut =
      signOut;
  }, [signOut]);
  return null;
}

function ClerkBridge({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, getToken, signOut } = useClerkAuth();
  const { user } = useUser();
  const [ready, setReady] = useState(false);

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
        const token = await getToken();
        if (token) setToken(token);
        const me = await fetchMe();
        if (me.tenant_id) setTenantId(me.tenant_id);
      } catch {
        /* org claim may lag */
      }
      if (!cancelled) setReady(true);
    }
    sync();
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
        <div className="login-card">Loading CMUL8 auth…</div>
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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {CLERK_PK ? (
      <ClerkProvider publishableKey={CLERK_PK} afterSignOutUrl="/">
        <ClerkBridge>
          <App clerkEnabled />
        </ClerkBridge>
      </ClerkProvider>
    ) : (
      <App clerkEnabled={false} />
    )}
  </StrictMode>,
);
