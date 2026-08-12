import { LogOut, Shield, UserRound, Building2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { AuthSession, AuthUser, Tenant } from "../api";
import { setTenantId } from "../api";
import { AdminPage } from "./AdminPage";
import { GovernancePage } from "./GovernancePage";
import { LoginPage } from "./LoginPage";

export type ProfileTab = "account" | "policy" | "admin" | "auth";

type Props = {
  open: boolean;
  onClose?: () => void;
  locked?: boolean;
  user: AuthUser | null;
  tenants: Tenant[];
  tenantId?: string;
  onTenant?: (id: string) => void;
  clerkEnabled?: boolean;
  clerkAvailable?: boolean;
  onUseClerk?: () => void;
  onAuthed: (session: AuthSession) => void;
  onSignOut: () => void;
  initialTab?: ProfileTab;
  /** Prefer register when guest gate asks to create an account */
  authMode?: "login" | "register";
};

export function ProfileManageModal({
  open,
  onClose,
  locked = false,
  user,
  tenants,
  tenantId,
  onTenant,
  clerkEnabled = false,
  clerkAvailable = false,
  onUseClerk,
  onAuthed,
  onSignOut,
  initialTab,
  authMode = "login",
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

  const isGuestAuth = !user && tab === "auth";
  const title =
    tab === "policy" ? "Policy" : tab === "admin" ? "Admin" : tab === "auth" ? (user ? "Session" : "Sign in") : "Account";

  return (
    <div className="acct-backdrop" role="dialog" aria-modal="true" aria-label="Account">
      <div
        className={`acct-modal ${isGuestAuth ? "acct-modal-auth" : ""} ${
          tab === "policy" || tab === "admin" ? "acct-modal-wide" : ""
        }`}
      >
        {!isGuestAuth && (
          <aside className="acct-rail">
            <div className="acct-rail-head">
              <span className="acct-brand">Simulacra</span>
              <p className="acct-rail-sub">Workspace controls</p>
            </div>

            <nav className="acct-nav" aria-label="Account sections">
              {user ? (
                <>
                  <button type="button" className={tab === "account" ? "active" : ""} onClick={() => setTab("account")}>
                    <UserRound size={15} strokeWidth={1.75} />
                    Account
                  </button>
                  <button type="button" className={tab === "policy" ? "active" : ""} onClick={() => setTab("policy")}>
                    <Shield size={15} strokeWidth={1.75} />
                    Policy
                  </button>
                  <button type="button" className={tab === "admin" ? "active" : ""} onClick={() => setTab("admin")}>
                    <Building2 size={15} strokeWidth={1.75} />
                    Admin
                  </button>
                </>
              ) : (
                <button type="button" className={tab === "auth" ? "active" : ""} onClick={() => setTab("auth")}>
                  <UserRound size={15} strokeWidth={1.75} />
                  Sign in
                </button>
              )}
            </nav>

            {user && (
              <button type="button" className="acct-signout" onClick={onSignOut}>
                <LogOut size={15} strokeWidth={1.75} />
                Sign out
              </button>
            )}
          </aside>
        )}

        <section className={`acct-main ${tab === "policy" || tab === "admin" ? "acct-main-wide" : ""}`}>
          {!locked && onClose && (
            <button type="button" className="acct-close" onClick={onClose} aria-label="Close">
              <X size={16} />
            </button>
          )}

          {tab === "account" && user && (
            <div className="acct-pane">
              <h1 className="acct-title">{title}</h1>
              <p className="acct-sub">Identity, workspace, and session.</p>

              <div className="acct-card">
                <div className="acct-kv">
                  <span>Email</span>
                  <strong>{user.email}</strong>
                </div>
                <div className="acct-kv">
                  <span>Name</span>
                  <strong>{user.name || "—"}</strong>
                </div>
                <div className="acct-kv">
                  <span>Role</span>
                  <strong>{user.is_platform_admin ? "Platform admin" : "Member"}</strong>
                </div>
              </div>

              <h3 className="acct-section">Workspace</h3>
              {tenants.length > 1 && onTenant ? (
                <label className="acct-select-wrap">
                  <span>Active workspace</span>
                  <select
                    className="acct-select"
                    value={tenantId || tenants[0]?.id}
                    onChange={(e) => {
                      setTenantId(e.target.value);
                      onTenant(e.target.value);
                    }}
                  >
                    {tenants.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <ul className="acct-tenant-list">
                {tenants.map((t) => (
                  <li key={t.id} className={t.id === (tenantId || tenants[0]?.id) ? "on" : ""}>
                    <strong>{t.name}</strong>
                    <span>{t.id}</span>
                  </li>
                ))}
                {tenants.length === 0 && <li className="dim">No workspaces yet</li>}
              </ul>

              <h3 className="acct-section">Preferences</h3>
              <div className="acct-card">
                <div className="acct-kv">
                  <span>Theme</span>
                  <strong>Void</strong>
                </div>
                <div className="acct-kv">
                  <span>Auth</span>
                  <strong>{clerkAvailable ? "Password + CMUL8 Clerk" : "Password"}</strong>
                </div>
              </div>
            </div>
          )}

          {tab === "auth" && (
            <div className="acct-pane acct-pane-auth">
              {isGuestAuth && (
                <div className="acct-auth-brand">
                  Simu<em>lacra</em>
                </div>
              )}
              <h1 className="acct-title">{user ? "Session" : "Sign in"}</h1>
              <p className="acct-sub">
                {user
                  ? "You’re signed in. Use Sign out to end this session."
                  : "Continue to your workspace."}
              </p>
              {!user && (
                <LoginPage
                  key={authMode}
                  embedded
                  clerkEnabled={clerkEnabled}
                  clerkAvailable={clerkAvailable}
                  onUseClerk={onUseClerk}
                  onAuthed={onAuthed}
                  initialMode={authMode}
                />
              )}
              {user && (
                <div className="acct-session-ok">
                  Signed in as <strong>{user.email}</strong>
                </div>
              )}
            </div>
          )}

          {tab === "policy" && user && (
            <div className="acct-pane acct-pane-embed">
              <h1 className="acct-title">Policy</h1>
              <p className="acct-sub">Control plane for AI-generated internal apps.</p>
              <GovernancePage embedded onBack={() => setTab("account")} />
            </div>
          )}

          {tab === "admin" && user && (
            <div className="acct-pane acct-pane-embed">
              <h1 className="acct-title">Admin</h1>
              <p className="acct-sub">Tenants, members, keys, and sandbox policy.</p>
              <AdminPage embedded onBack={() => setTab("account")} />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
