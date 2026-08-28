import { SignIn, SignUp } from "@clerk/clerk-react";
import { useEffect, useState } from "react";
import { acceptCmul8Invitation, forgotPassword, login, register, resetPassword, type AuthSession } from "../api";
import {
  clearMissionInvitation,
  missionInvitationFromLocation,
  rememberMissionInvitationFromLocation,
} from "../features/team/missionInvitation";

type Props = {
  onAuthed: (session: AuthSession) => void;
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
  /** Render without full-page chrome (inside profile modal) */
  embedded?: boolean;
  /** Prefer sign-up when opened from the landing guest gate */
  initialMode?: "login" | "register";
};

type Mode = "login" | "register" | "forgot" | "reset";

function tokenFromLocation(): string {
  if (typeof window === "undefined") return "";
  const hash = window.location.hash || "";
  const fromHash = hash.match(/(?:^|#|&)reset=([^&]+)/);
  if (fromHash?.[1]) return decodeURIComponent(fromHash[1]);
  const q = new URLSearchParams(window.location.search).get("reset_token");
  return q ? decodeURIComponent(q) : "";
}

export function LoginPage({
  onAuthed,
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
  embedded = false,
  initialMode = "login",
}: Props) {
  const bootToken = tokenFromLocation();
  const [invitation] = useState(() => missionInvitationFromLocation());
  const [mode, setMode] = useState<Mode>(bootToken ? "reset" : initialMode);
  const [clerkMode, setClerkMode] = useState<"sign-in" | "sign-up">(
    initialMode === "register" ? "sign-up" : "sign-in",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [resetToken, setResetToken] = useState(bootToken);
  const [resetLink, setResetLink] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    const t = tokenFromLocation();
    if (t) {
      setResetToken(t);
      setMode("reset");
    }
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      if (mode === "forgot") {
        const res = await forgotPassword(email);
        if (res.reset_url) {
          setResetLink(res.reset_url.startsWith("http") ? res.reset_url : `${window.location.origin}/${res.reset_url.replace(/^\//, "")}`);
          setInfo("Reset link ready — open it to choose a new password. It expires in about an hour.");
        } else {
          setInfo("If that email has an account, a reset link was prepared. Check with your admin if you don’t see one.");
        }
        return;
      }
      if (mode === "reset") {
        if (password !== password2) throw new Error("Passwords do not match");
        const res = await resetPassword(resetToken.trim(), password);
        setInfo(`Password updated for ${res.email}. Sign in with your new password.`);
        setMode("login");
        setPassword("");
        setPassword2("");
        setResetToken("");
        if (window.location.hash.includes("reset=")) {
          window.history.replaceState(null, "", window.location.pathname + window.location.search);
        }
        return;
      }
      const session =
        mode === "login"
          ? await login(email, password)
          : await register(email, password, name);
      if (invitation) {
        await acceptCmul8Invitation(invitation.missionId, invitation.invitationId, {
          client_request_id: invitation.clientRequestId,
          token: invitation.token,
        });
        clearMissionInvitation();
      }
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

  const title =
    mode === "forgot" ? "Reset password" : mode === "reset" ? "Choose a new password" : "Missions";
  const sub =
    mode === "forgot"
      ? "We’ll create a one-time link for your account"
      : mode === "reset"
        ? "This link works once and expires in about an hour"
        : invitation ? "Sign in to join this Mission" : "Human-led agent teams";

  const passwordBlock = !clerkEnabled && (
    <div className={embedded ? "login-embed" : "login-card"}>
      {!embedded && (
        <>
          <h1>{title}</h1>
          <p className="login-sub">{sub}</p>
        </>
      )}
      {invitation && (mode === "login" || mode === "register") ? <div className="login-invitation" role="status"><strong>You’re joining a Mission</strong><span>Sign in with the invited email. Missions will finish joining you securely.</span></div> : null}
      {(mode === "login" || mode === "register") && (
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
      )}
      <form onSubmit={submit}>
        {mode === "register" && (
          <>
            <label>
              Your name
              <input value={name} onChange={(e) => setName(e.target.value)} disabled={busy} autoComplete="name" />
            </label>
          </>
        )}
        {(mode === "login" || mode === "register" || mode === "forgot") && (
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
        )}
        {mode === "reset" && (
          <label>
            Reset token
            <input
              value={resetToken}
              onChange={(e) => setResetToken(e.target.value)}
              required
              disabled={busy}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        )}
        {(mode === "login" || mode === "register" || mode === "reset") && (
          <label>
            {mode === "reset" ? "New password" : "Password"}
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
        )}
        {mode === "reset" && (
          <label>
            Confirm password
            <input
              type="password"
              value={password2}
              onChange={(e) => setPassword2(e.target.value)}
              required
              minLength={8}
              disabled={busy}
              autoComplete="new-password"
            />
          </label>
        )}
        {error && <div className="landing-error">{error}</div>}
        {info && <p className="login-hint">{info}</p>}
        {resetLink && (
          <p className="login-reset-link">
            <a href={resetLink}>{resetLink}</a>
          </p>
        )}
        <button type="submit" className="approve-btn" disabled={busy}>
          {busy
            ? "…"
            : mode === "login"
              ? "Sign in"
              : mode === "register"
                ? "Create account"
                : mode === "forgot"
                  ? "Get reset link"
                  : "Update password"}
        </button>
      </form>
      {mode === "login" && (
        <button
          type="button"
          className="ghost-btn wide"
          onClick={() => {
            setMode("forgot");
            setError(null);
            setInfo(null);
            setResetLink(null);
          }}
        >
          Forgot password?
        </button>
      )}
      {(mode === "forgot" || mode === "reset") && (
        <button
          type="button"
          className="ghost-btn wide"
          onClick={() => {
            setMode("login");
            setError(null);
            setInfo(null);
            setResetLink(null);
          }}
        >
          Back to sign in
        </button>
      )}
      {clerkAvailable && onUseClerk && (mode === "login" || mode === "register") && (
        <button type="button" className="ghost-btn wide" onClick={() => {
          rememberMissionInvitationFromLocation();
          onUseClerk();
        }}>
          Continue with Missions sign-in
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
