import { ArrowLeft, Box, Building2, Plus, Shield } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  createTenant,
  fetchAdmin,
  fetchSandbox,
  updateTenant,
  type AdminOverview,
  type SandboxStatus,
  type Tenant,
} from "../api";

type Props = { onBack: () => void };

export function AdminPage({ onBack }: Props) {
  const [admin, setAdmin] = useState<AdminOverview | null>(null);
  const [sandbox, setSandbox] = useState<SandboxStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([fetchAdmin(), fetchSandbox()]);
      setAdmin(a);
      setSandbox(s);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleCreate() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await createTenant(name.trim(), {
        sandbox: sandbox?.active === "docker" ? "docker" : "worktree",
        network: "deny",
      });
      setName("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggleStatus(t: Tenant) {
    setBusy(true);
    try {
      await updateTenant(t.id, { status: t.status === "active" ? "suspended" : "active" });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-page">
      <header className="admin-top">
        <button type="button" className="ghost-btn" onClick={onBack}>
          <ArrowLeft size={14} />
          Back
        </button>
        <h1>
          <Building2 size={18} />
          Multi-tenant admin
        </h1>
      </header>

      {error && <div className="landing-error">{error}</div>}

      <section className="admin-section">
        <h2>
          <Box size={16} />
          Sandbox
        </h2>
        {sandbox && (
          <div className="admin-card">
            <div className="admin-kv">
              <span>Active mode</span>
              <strong>{sandbox.active}</strong>
            </div>
            <div className="admin-kv">
              <span>Requested</span>
              <strong>{sandbox.requested}</strong>
            </div>
            <div className="admin-kv">
              <span>Docker available</span>
              <strong>{sandbox.docker_available ? "yes" : "no"}</strong>
            </div>
            <p className="admin-note">{sandbox.trust_model}</p>
            {!sandbox.docker_available && (
              <p className="admin-note warn">
                Install Docker Desktop to enable full container isolation. Until then Simulacra uses a
                worktree jail (cwd + scrubbed env
                {typeof navigator !== "undefined" && navigator.platform?.includes("Mac")
                  ? " + macOS seatbelt when available"
                  : ""}
                ).
              </p>
            )}
          </div>
        )}
      </section>

      <section className="admin-section">
        <h2>
          <Shield size={16} />
          Tenants
        </h2>
        <div className="admin-create">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New tenant name"
            disabled={busy}
          />
          <button type="button" className="approve-btn" disabled={busy || !name.trim()} onClick={handleCreate}>
            <Plus size={14} />
            Create
          </button>
        </div>

        <div className="admin-tenant-list">
          {(admin?.tenants || []).map((t) => (
            <article key={t.id} className="admin-card tenant-row">
              <div>
                <strong>{t.name}</strong>
                <div className="admin-meta">
                  {t.id} · {t.project_count ?? 0} projects · sandbox {t.policy?.sandbox}
                </div>
              </div>
              <div className="admin-row-actions">
                <span className={`source-chip source-${t.status === "active" ? "prime" : "error"}`}>
                  {t.status}
                </span>
                <button type="button" className="ghost-btn" disabled={busy} onClick={() => toggleStatus(t)}>
                  {t.status === "active" ? "Suspend" : "Activate"}
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      {admin && (
        <footer className="admin-footer">
          {admin.totals.active_tenants} active / {admin.totals.tenants} tenants · {admin.totals.projects}{" "}
          projects · default {admin.default_tenant}
        </footer>
      )}
    </div>
  );
}
