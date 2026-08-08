import { SignIn } from "@clerk/clerk-react";
import { useState } from "react";
import { login, register, type AuthSession } from "../api";

type Props = {
  onAuthed: (session: AuthSession) => void;
  clerkEnabled?: boolean;
};

export function LoginPage({ onAuthed, clerkEnabled = false }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("admin@localhost");
  const [password, setPassword] = useState("simulacra-admin-change-me");
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
          : await register(email, password, name, tenantName || undefined);
      onAuthed(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auth failed");
    } finally {
      setBusy(false);
    }
  }

  if (clerkEnabled) {
    return (
      <div className="login-page">
        <div className="login-card clerk-card">
          <h1>Simulacra</h1>
          <p className="login-sub">Sign in with your CMUL8 Clerk account</p>
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
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>Simulacra</h1>
        <p className="login-sub">Multi-tenant governed data apps</p>
        <div className="login-tabs">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
            Sign in
          </button>
          <button
            type="button"
            className={mode === "register" ? "active" : ""}
            onClick={() => setMode("register")}
          >
            Create workspace
          </button>
        </div>
        <form onSubmit={submit}>
          {mode === "register" && (
            <>
              <label>
                Your name
                <input value={name} onChange={(e) => setName(e.target.value)} disabled={busy} />
              </label>
              <label>
                Workspace name
                <input
                  value={tenantName}
                  onChange={(e) => setTenantName(e.target.value)}
                  placeholder="Acme Risk"
                  disabled={busy}
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
            />
          </label>
          {error && <div className="landing-error">{error}</div>}
          <button type="submit" className="approve-btn" disabled={busy}>
            {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}
