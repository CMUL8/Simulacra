import { LogOut, Settings, UserRound, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { AuthSession, AuthUser, Tenant } from "../api";
import { LoginPage } from "./LoginPage";

export type ProfileTab = "account" | "auth" | "settings";

type Props = {
  open: boolean;
  onClose?: () => void;
  /** Force open for unauthenticated gate — close disabled */
  locked?: boolean;
  user: AuthUser | null;
  tenants: Tenant[];
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
  onAuthed: (session: AuthSession) => void;
  onSignOut: () => void;
  initialTab?: ProfileTab;
};

export function ProfileManageModal({
  open,
  onClose,
  locked = false,
  user,
  tenants,
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
  onAuthed,
  onSignOut,
  initialTab,
}: Props) {
  const [tab, setTab] = useState<ProfileTab>(initialTab || (user ? "account" : "auth"));

  useEffect(() => {
    if (open) setTab(initialTab || (user ? "account" : "auth"));
  }, [open, user, initialTab]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !locked && onClose) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, locked, onClose]);

  if (!open) return null;

  return (
    <div className="profile-backdrop" role="dialog" aria-modal="true" aria-label="Manage profile">
      <div className="profile-modal">
        <aside className="profile-rail">
          <div className="profile-rail-head">
            <span className="profile-kicker">Account</span>
            <h2 className="profile-rail-title">Manage</h2>
          </div>
          <nav className="profile-nav">
            {user && (
              <button
                type="button"
                className={tab === "account" ? "active" : ""}
                onClick={() => setTab("account")}
              >
                <UserRound size={15} />
                Profile
              </button>
            )}
            <button type="button" className={tab === "auth" ? "active" : ""} onClick={() => setTab("auth")}>
              <UserRound size={15} />
              {user ? "Session" : "Sign in"}
            </button>
            <button
              type="button"
              className={tab === "settings" ? "active" : ""}
              onClick={() => setTab("settings")}
            >
              <Settings size={15} />
              Settings
            </button>
          </nav>
          {user && (
            <button type="button" className="profile-signout" onClick={onSignOut}>
              <LogOut size={15} />
              Sign out
            </button>
          )}
        </aside>

        <section className="profile-main">
          {!locked && onClose && (
            <button type="button" className="profile-close" onClick={onClose} aria-label="Close">
              <X size={16} />
            </button>
          )}

          {tab === "account" && user && (
            <div className="profile-pane">
              <h1 className="profile-pane-title">Profile</h1>
              <p className="profile-pane-sub">Your Simulacra identity and workspaces.</p>
              <div className="profile-kv">
                <span>Email</span>
                <strong>{user.email}</strong>
              </div>
              <div className="profile-kv">
                <span>Name</span>
                <strong>{user.name || "—"}</strong>
              </div>
              <div className="profile-kv">
                <span>Role</span>
                <strong>{user.is_platform_admin ? "Platform admin" : "Member"}</strong>
              </div>
              <h3 className="profile-section-label">Workspaces</h3>
              <ul className="profile-tenant-list">
                {tenants.map((t) => (
                  <li key={t.id}>
                    <strong>{t.name}</strong>
                    <span>{t.id}</span>
                  </li>
                ))}
                {tenants.length === 0 && <li className="dim">No workspaces yet</li>}
              </ul>
            </div>
          )}

          {tab === "auth" && (
            <div className="profile-pane profile-pane-auth">
              <h1 className="profile-pane-title">{user ? "Session" : "Welcome"}</h1>
              <p className="profile-pane-sub">
                {user
                  ? "You’re signed in. Use Sign out in the rail to end this session."
                  : "Sign in or create a workspace to start building governed data apps."}
              </p>
              {!user && (
                <LoginPage
                  embedded
                  clerkEnabled={clerkEnabled}
                  clerkAvailable={clerkAvailable}
                  onUseClerk={onUseClerk}
                  onAuthed={onAuthed}
                />
              )}
              {user && (
                <div className="profile-session-ok">
                  Signed in as <strong>{user.email}</strong>
                </div>
              )}
            </div>
          )}

          {tab === "settings" && (
            <div className="profile-pane">
              <h1 className="profile-pane-title">Settings</h1>
              <p className="profile-pane-sub">Appearance follows the Simulacra void theme — Anything-grade contrast.</p>
              <div className="profile-kv">
                <span>Theme</span>
                <strong>Void / bone</strong>
              </div>
              <div className="profile-kv">
                <span>Auth</span>
                <strong>{clerkAvailable ? "Password + CMUL8 Clerk" : "Password"}</strong>
              </div>
              <div className="profile-kv">
                <span>Sandbox</span>
                <strong>Tenant policy</strong>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
