import { ArrowLeft, Box, Building2, KeyRound, Plus, Shield, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  createApiKey,
  createTenant,
  fetchAdmin,
  fetchPlatformAudit,
  fetchSandbox,
  getTenantId,
  getToken,
  inviteMember,
  listApiKeys,
  listMembers,
  removeMember,
  revokeApiKey,
  setTenantId,
  updateTenant,
  type AdminOverview,
  type ApiKeyMeta,
  type SandboxStatus,
  type Tenant,
  type TenantMember,
} from "../api";

type Props = { onBack: () => void };

async function downloadAudit(format: string) {
  const token = getToken();
  const base = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "" : "/api");
  const res = await fetch(`${base}/admin/audit/export?format=${encodeURIComponent(format)}&limit=1000`, {
    headers: {
      Authorization: token ? `Bearer ${token}` : "",
      "X-Tenant-Id": getTenantId(),
    },
  });
  if (!res.ok) throw new Error(await res.text());
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `simulacra-audit.${format === "cef" ? "cef" : "ndjson"}`;
  a.click();
  URL.revokeObjectURL(url);
}

export function AdminPage({ onBack }: Props) {
  const [admin, setAdmin] = useState<AdminOverview | null>(null);
  const [sandbox, setSandbox] = useState<SandboxStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState(getTenantId());
  const [members, setMembers] = useState<TenantMember[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [invitePassword, setInvitePassword] = useState("");
  const [keys, setKeys] = useState<ApiKeyMeta[]>([]);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [audit, setAudit] = useState<Record<string, unknown>[]>([]);
  const [quota, setQuota] = useState("50");

  const refresh = useCallback(async () => {
    try {
      const [a, s, k] = await Promise.all([fetchAdmin(), fetchSandbox(), listApiKeys()]);
      setAdmin(a);
      setSandbox(s);
      setKeys(k.filter((x) => !x.revoked));
      setError(null);
      const tid = selectedId || a.tenants[0]?.id || getTenantId();
      if (tid && tid !== "*") {
        setMembers(await listMembers(tid));
        const t = a.tenants.find((x) => x.id === tid);
        if (t?.policy?.max_projects != null) setQuota(String(t.policy.max_projects));
      }
      try {
        const aud = await fetchPlatformAudit(40);
        setAudit(aud.events || []);
      } catch {
        setAudit([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin");
    }
  }, [selectedId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleCreate() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const t = await createTenant(name.trim(), {
        sandbox: sandbox?.active === "docker" ? "docker" : "worktree",
        network: "deny",
        max_projects: 50,
      });
      setName("");
      setSelectedId(t.id);
      setTenantId(t.id);
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

  async function saveQuota() {
    if (!selectedId) return;
    setBusy(true);
    try {
      await updateTenant(selectedId, { policy: { max_projects: Number(quota) || 50 } });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Quota update failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleInvite() {
    if (!selectedId || !inviteEmail.trim()) return;
    setBusy(true);
    try {
      await inviteMember(
        selectedId,
        inviteEmail.trim(),
        inviteRole,
        invitePassword.trim() || undefined,
      );
      setInviteEmail("");
      setInvitePassword("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invite failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(userId: string) {
    if (!selectedId) return;
    setBusy(true);
    try {
      await removeMember(selectedId, userId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Remove failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateKey() {
    setBusy(true);
    try {
      const res = await createApiKey(`key-${Date.now().toString(36)}`);
      setNewKey(res.api_key);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Key create failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevokeKey(id: string) {
    setBusy(true);
    try {
      await revokeApiKey(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revoke failed");
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
          Enterprise admin
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
          </div>
        )}
      </section>

      <section className="admin-section">
        <h2>
          <Shield size={16} />
          Workspaces (tenants)
        </h2>
        <div className="admin-create">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New workspace name"
            disabled={busy}
          />
          <button type="button" className="approve-btn" disabled={busy || !name.trim()} onClick={handleCreate}>
            <Plus size={14} />
            Create
          </button>
        </div>

        <div className="admin-tenant-list">
          {(admin?.tenants || []).map((t) => (
            <article
              key={t.id}
              className={`admin-card tenant-row ${selectedId === t.id ? "selected" : ""}`}
              onClick={() => {
                setSelectedId(t.id);
                setTenantId(t.id);
              }}
            >
              <div>
                <strong>{t.name}</strong>
                <div className="admin-meta">
                  {t.id} · {t.project_count ?? 0} projects · max {t.policy?.max_projects ?? 50} · sandbox{" "}
                  {t.policy?.sandbox}
                </div>
              </div>
              <div className="admin-row-actions">
                <span className={`source-chip source-${t.status === "active" ? "prime" : "error"}`}>
                  {t.status}
                </span>
                <button
                  type="button"
                  className="ghost-btn"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleStatus(t);
                  }}
                >
                  {t.status === "active" ? "Suspend" : "Activate"}
                </button>
              </div>
            </article>
          ))}
        </div>

        {selectedId && (
          <div className="admin-card" style={{ marginTop: 12 }}>
            <div className="admin-kv">
              <span>Project quota</span>
              <div className="admin-create" style={{ margin: 0 }}>
                <input value={quota} onChange={(e) => setQuota(e.target.value)} disabled={busy} />
                <button type="button" className="ghost-btn" disabled={busy} onClick={saveQuota}>
                  Save
                </button>
              </div>
            </div>
          </div>
        )}
      </section>

      <section className="admin-section">
        <h2>
          <Users size={16} />
          Members
        </h2>
        <div className="admin-create">
          <input
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            placeholder="email@company.com"
            disabled={busy}
          />
          <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} disabled={busy}>
            <option value="viewer">viewer</option>
            <option value="member">member</option>
            <option value="admin">admin</option>
            <option value="owner">owner</option>
          </select>
          <input
            value={invitePassword}
            onChange={(e) => setInvitePassword(e.target.value)}
            placeholder="temp password (new users)"
            disabled={busy}
            type="password"
          />
          <button type="button" className="approve-btn" disabled={busy || !inviteEmail.trim()} onClick={handleInvite}>
            Invite
          </button>
        </div>
        <div className="admin-tenant-list">
          {members.map((m) => (
            <article key={m.user.id} className="admin-card tenant-row">
              <div>
                <strong>{m.user.email}</strong>
                <div className="admin-meta">
                  {m.user.name} · {m.role}
                </div>
              </div>
              <button type="button" className="ghost-btn" disabled={busy} onClick={() => handleRemove(m.user.id)}>
                Remove
              </button>
            </article>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h2>
          <KeyRound size={16} />
          API keys
        </h2>
        {newKey && (
          <div className="admin-card">
            <p className="admin-note warn">Copy now — shown once:</p>
            <code style={{ wordBreak: "break-all", fontSize: 12 }}>{newKey}</code>
          </div>
        )}
        <button type="button" className="approve-btn" disabled={busy} onClick={handleCreateKey}>
          <Plus size={14} />
          Create API key
        </button>
        <div className="admin-tenant-list" style={{ marginTop: 12 }}>
          {keys.map((k) => (
            <article key={k.id} className="admin-card tenant-row">
              <div>
                <strong>{k.name}</strong>
                <div className="admin-meta">
                  {k.prefix}… · {k.created_at}
                </div>
              </div>
              <button type="button" className="ghost-btn" disabled={busy} onClick={() => handleRevokeKey(k.id)}>
                Revoke
              </button>
            </article>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h2>Audit trail / SIEM</h2>
        <div className="admin-create" style={{ marginBottom: 12 }}>
          <button type="button" className="ghost-btn" disabled={busy} onClick={() => downloadAudit("json").catch((e) => setError(String(e.message || e)))}>
            Export NDJSON
          </button>
          <button type="button" className="ghost-btn" disabled={busy} onClick={() => downloadAudit("cef").catch((e) => setError(String(e.message || e)))}>
            Export CEF
          </button>
          <button type="button" className="ghost-btn" disabled={busy} onClick={() => downloadAudit("hec").catch((e) => setError(String(e.message || e)))}>
            Export Splunk HEC
          </button>
        </div>
        <div className="admin-card" style={{ maxHeight: 240, overflow: "auto" }}>
          {audit.length === 0 && <p className="admin-note">No events yet.</p>}
          {audit.map((e, i) => (
            <div key={i} className="admin-meta" style={{ marginBottom: 6 }}>
              {String(e.ts || "")} · {String(e.action || "")} · tenant {String(e.tenant_id || "")}
            </div>
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
