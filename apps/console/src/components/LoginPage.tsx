import { SignIn, SignUp } from "@clerk/clerk-react";
import { useState } from "react";
import { login, register, type AuthSession } from "../api";

type Props = {
  onAuthed: (session: AuthSession) => void;
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
  /** Render without full-page chrome (inside profile modal) */
  embedded?: boolean;
};

export function LoginPage({
  onAuthed,
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
  embedded = false,
}: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [clerkMode, setClerkMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session =
        mode === "login"
          ? await login(email, password)
          : await register(email, password, name, tenantName.trim() || undefined);
      onAuthed(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auth failed");
    } finally {
      setBusy(false);
    }
  }

  const clerkBlock = clerkEnabled && (
    <div className="login-embed clerk-card">
      <div className="login-tabs">
        <button
          type="button"
          className={clerkMode === "sign-in" ? "active" : ""}
          onClick={() => setClerkMode("sign-in")}
        >
          Sign in
        </button>
        <button
          type="button"
          className={clerkMode === "sign-up" ? "active" : ""}
          onClick={() => setClerkMode("sign-up")}
        >
          Sign up
        </button>
      </div>
      {clerkMode === "sign-in" ? (
        <SignIn
          routing="hash"
          fallbackRedirectUrl="/"
          forceRedirectUrl="/"
          appearance={{
            elements: {
              rootBox: "clerk-root",
              card: "clerk-inner-card",
            },
          }}
        />
      ) : (
        <SignUp
          routing="hash"
          fallbackRedirectUrl="/"
          forceRedirectUrl="/"
          appearance={{
            elements: {
              rootBox: "clerk-root",
              card: "clerk-inner-card",
            },
          }}
        />
      )}
      <button type="button" className="ghost-btn wide" onClick={() => window.location.reload()}>
        Use password instead
      </button>
    </div>
  );

  const passwordBlock = !clerkEnabled && (
    <div className={embedded ? "login-embed" : "login-card"}>
      {!embedded && (
        <>
          <h1>Simulacra</h1>
          <p className="login-sub">Governed data apps</p>
        </>
      )}
      <div className="login-tabs">
        <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
          Sign in
        </button>
        <button
          type="button"
          className={mode === "register" ? "active" : ""}
          onClick={() => setMode("register")}
        >
          Sign up
        </button>
      </div>
      <form onSubmit={submit}>
        {mode === "register" && (
          <>
            <label>
              Your name
              <input value={name} onChange={(e) => setName(e.target.value)} disabled={busy} autoComplete="name" />
            </label>
            <label>
              Workspace name
              <input
                value={tenantName}
                onChange={(e) => setTenantName(e.target.value)}
                placeholder="Acme Risk"
                disabled={busy}
                required
              />
            </label>
          </>
        )}
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={busy}
            autoComplete="username"
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            disabled={busy}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </label>
        {error && <div className="landing-error">{error}</div>}
        <button type="submit" className="approve-btn" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
      {clerkAvailable && onUseClerk && (
        <button type="button" className="ghost-btn wide" onClick={onUseClerk}>
          Continue with CMUL8 Clerk
        </button>
      )}
    </div>
  );

  const body = (
    <>
      {clerkBlock}
      {passwordBlock}
    </>
  );

  if (embedded) return <div className="login-embedded-root">{body}</div>;

  return <div className="login-page">{body}</div>;
}
