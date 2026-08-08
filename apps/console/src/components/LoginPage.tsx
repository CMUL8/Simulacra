import { SignIn } from "@clerk/clerk-react";
import { useState } from "react";
import { login, type AuthSession } from "../api";

type Props = {
  onAuthed: (session: AuthSession) => void;
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
};

export function LoginPage({
  onAuthed,
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
}: Props) {
  const [email, setEmail] = useState("admin@cmul8.com");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onAuthed(await login(email, password));
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
            signUpUrl={undefined}
            appearance={{
              elements: {
                rootBox: "clerk-root",
                card: "clerk-inner-card",
                footerAction: { display: "none" },
              },
            }}
          />
          <button
            type="button"
            className="ghost-btn"
            style={{ marginTop: 16, width: "100%" }}
            onClick={() => window.location.reload()}
          >
            Back to password login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>Simulacra</h1>
        <p className="login-sub">Sign in to continue</p>
        <form onSubmit={submit}>
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
              autoComplete="current-password"
            />
          </label>
          {error && <div className="landing-error">{error}</div>}
          <button type="submit" className="approve-btn" disabled={busy}>
            {busy ? "…" : "Sign in"}
          </button>
        </form>
        {clerkAvailable && onUseClerk && (
          <button type="button" className="ghost-btn" style={{ marginTop: 14, width: "100%" }} onClick={onUseClerk}>
            Sign in with CMUL8 Clerk
          </button>
        )}
      </div>
    </div>
  );
}
