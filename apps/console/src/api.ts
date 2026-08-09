const API = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "" : "/api");

export type ChatMessage = { role: string; content: string; at?: string; source?: string | null };
export type AppConfig = {
  title: string;
  subtitle: string;
  search_enabled?: boolean;
  sort_column?: string;
  group_by?: string | null;
};

export type DesignBrief = {
  product_name?: string;
  one_liner?: string;
  audience?: string;
  aesthetic?: {
    direction?: string;
    density?: string;
    color_mode?: string;
    palette?: Record<string, string>;
    typography?: Record<string, string>;
    shape?: string;
    chrome?: string;
    motion?: string;
  };
  information_architecture?: {
    primary_view?: string;
    must_have?: string[];
    must_not?: string[];
  };
  copy_tone?: string;
  references?: string[];
  user_notes?: string;
};

export type TenantPolicy = {
  sandbox?: string;
  network?: string;
  max_concurrent_jobs?: number;
  max_projects?: number;
  max_jobs_per_day?: number;
  allowed_models?: string[];
  retention_days?: number;
  require_approve?: boolean;
  sso_enforced?: boolean;
};

export type Tenant = {
  id: string;
  name: string;
  status: string;
  created_at?: string;
  notes?: string;
  policy?: TenantPolicy;
  project_count?: number;
};

export type AdminOverview = {
  tenants: Tenant[];
  sandbox_mode: string;
  default_tenant: string;
  totals: { tenants: number; projects: number; active_tenants: number };
};

export type SandboxStatus = {
  requested: string;
  active: string;
  docker_available: boolean;
  image?: string | null;
  network: string;
  trust_model: string;
};

export type JobState = {
  id?: string | null;
  kind?: string | null;
  status?: string;
  steps?: number;
  max_steps?: number;
  cancel_requested?: boolean;
  error?: string | null;
  label?: string | null;
};

export type Checkpoint = { id: string; label: string; created_at: string };
export type DataRoomFile = {
  name: string;
  size: number;
  type: string;
  status?: string;
  detail?: string;
  sha256?: string;
  row_count?: number;
};

export type DataProfile = {
  row_count?: number;
  columns?: string[];
  vendors?: string[];
  themes?: string[];
  high_risk?: number;
  medium_risk?: number;
  low_risk?: number;
  regions?: string[];
  owners?: string[];
  source_files?: string[];
  empty_room?: boolean;
  suggested_primary?: string;
  suggested_must_have?: string[];
  nuance_notes?: string[];
};

export type PlanPreview = {
  row_count: number;
  high_risk: number;
  medium_risk?: number;
  low_risk?: number;
  vendors: string[];
  themes?: string[];
  files: DataRoomFile[];
  summary: string;
  sample_rows: Record<string, unknown>[];
  profile?: DataProfile;
  extract?: {
    row_count?: number;
    errors?: string[];
    skipped?: string[];
    ok_files?: number;
  };
  fingerprint?: string;
  source_room?: {
    empty?: boolean;
    row_count?: number;
    file_count?: number;
    file_names?: string[];
    vendors?: string[];
    looks_like_vendor_sample?: boolean;
  };
};

export type Project = {
  id: string;
  prompt: string;
  goal: string;
  tenant_id?: string;
  phase: "plan" | "build" | "ready";
  plan_approved: boolean;
  status: string;
  artifact_kind?: "data_app" | "report" | "slides" | "one_pager" | string;
  gates_status: string;
  deployed: boolean;
  deploy_url: string | null;
  chat: ChatMessage[];
  app_config: AppConfig;
  row_count: number;
  checkpoints: Checkpoint[];
  active_checkpoint: number;
  plan_preview: PlanPreview;
  design_brief?: DesignBrief;
  prime?: {
    session_id?: string | null;
    model?: string | null;
    source?: string;
    last_error?: string | null;
    status?: string;
    steps?: number;
    /** Observed from Prime chat envelope: await_user | build | iterate | research */
    request?: string | null;
    brief?: string | null;
  };
  job?: JobState;
  sandbox?: SandboxStatus | Record<string, unknown>;
  created_at?: string;
};

export type Snapshot = {
  project: Project;
  preview_data: { columns: string[]; rows: Record<string, unknown>[]; row_count: number };
  preview_url: string | null;
  job?: JobState;
  job_id?: string;
  status?: string;
};

export type AgentEvent = {
  id: string;
  ts: string;
  type: "phase" | "tool" | "think" | "gate" | "message" | "error" | "done";
  label: string;
  detail: string;
  status: "running" | "done" | "fail";
  meta?: Record<string, unknown>;
};

export type GovernanceProject = {
  id: string;
  title: string;
  phase: string;
  status: string;
  gates_status: string;
  deployed: boolean;
  row_count: number;
  plan_approved: boolean;
  checkpoints: number;
  created_at: string;
  integration: { layer: string; direct_access: boolean; audit_logged: boolean };
  gates: { gate: string; passed: boolean; detail: string }[];
  deploy?: { preview_url?: string };
};

export type GovernanceOverview = {
  policy: { direct_system_access: boolean; message: string; description: string };
  summary: { total_projects: number; gates_pass: number; gates_fail: number; deployed: number; in_plan: number };
  projects: GovernanceProject[];
};

const TENANT_KEY = "simulacra_tenant_id";
const TOKEN_KEY = "simulacra_token";

export type AuthUser = {
  id: string;
  email: string;
  name: string;
  is_platform_admin?: boolean;
  status?: string;
};

export type AuthSession = {
  token: string;
  token_type: string;
  user: AuthUser;
  tenants: Tenant[];
  tenant_id: string;
};

export function getTenantId(): string {
  return localStorage.getItem(TENANT_KEY) || "default";
}

export function setTenantId(id: string) {
  localStorage.setItem(TENANT_KEY, id);
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (!token) localStorage.removeItem(TOKEN_KEY);
  else localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Tenant-Id": getTenantId(),
    ...((init?.headers as Record<string, string>) || {}),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export async function login(email: string, password: string): Promise<AuthSession> {
  const data = await json<AuthSession>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setToken(data.token);
  if (data.tenant_id) setTenantId(data.tenant_id);
  return data;
}

export async function register(
  email: string,
  password: string,
  name = "",
  tenantName?: string,
): Promise<AuthSession> {
  const data = await json<AuthSession>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, name, tenant_name: tenantName }),
  });
  setToken(data.token);
  if (data.tenant_id) setTenantId(data.tenant_id);
  return data;
}

export async function fetchMe(): Promise<{
  user: AuthUser;
  tenant_id: string;
  role: string;
  tenants: Tenant[];
}> {
  return json("/auth/me");
}

export async function listProjects(): Promise<Project[]> {
  const data = await json<{ projects: Project[] }>("/projects");
  return data.projects;
}

export async function listFixtureFiles(): Promise<DataRoomFile[]> {
  const data = await json<{ files: DataRoomFile[] }>("/fixtures/data-room");
  return data.files;
}

export async function listProjectFiles(id: string): Promise<DataRoomFile[]> {
  const data = await json<{ files: DataRoomFile[] }>(`/projects/${id}/files`);
  return data.files;
}

export async function fetchAudit(id: string): Promise<Record<string, unknown>> {
  return json(`/projects/${id}/audit`);
}

export async function fetchGovernance(): Promise<GovernanceOverview> {
  return json("/governance");
}

export async function fetchAdmin(): Promise<AdminOverview> {
  return json("/admin");
}

export async function fetchSandbox(): Promise<SandboxStatus> {
  return json("/admin/sandbox");
}

export async function listTenants(): Promise<Tenant[]> {
  const data = await json<{ tenants: Tenant[] }>("/tenants");
  return data.tenants;
}

export async function createTenant(name: string, policy?: TenantPolicy, notes = ""): Promise<Tenant> {
  const data = await json<{ tenant: Tenant }>("/tenants", {
    method: "POST",
    body: JSON.stringify({ name, policy, notes }),
  });
  return data.tenant;
}

export async function updateTenant(
  id: string,
  patch: { name?: string; status?: string; notes?: string; policy?: TenantPolicy },
): Promise<Tenant> {
  const data = await json<{ tenant: Tenant }>(`/tenants/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return data.tenant;
}

export type TenantMember = {
  user: AuthUser;
  role: string;
  created_at?: string;
};

export async function listMembers(tenantId: string): Promise<TenantMember[]> {
  const data = await json<{ members: TenantMember[] }>(`/tenants/${tenantId}/members`);
  return data.members;
}

export async function inviteMember(
  tenantId: string,
  email: string,
  role = "member",
  password?: string,
  name = "",
): Promise<{ user: AuthUser; role: string; created: boolean }> {
  return json(`/tenants/${tenantId}/members`, {
    method: "POST",
    body: JSON.stringify({ email, role, password: password || null, name }),
  });
}

export async function removeMember(tenantId: string, userId: string): Promise<void> {
  await json(`/tenants/${tenantId}/members/${userId}`, { method: "DELETE" });
}

export type ApiKeyMeta = {
  id: string;
  name: string;
  prefix: string;
  created_at?: string;
  revoked?: boolean;
};

export async function listApiKeys(): Promise<ApiKeyMeta[]> {
  const data = await json<{ keys: ApiKeyMeta[] }>("/auth/api-keys");
  return data.keys;
}

export async function createApiKey(name = "default"): Promise<{ api_key: string; key: ApiKeyMeta }> {
  return json("/auth/api-keys", { method: "POST", body: JSON.stringify({ name }) });
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await json(`/auth/api-keys/${keyId}`, { method: "DELETE" });
}

export async function fetchPlatformAudit(limit = 50): Promise<{ events: Record<string, unknown>[] }> {
  return json(`/admin/audit?limit=${limit}`);
}

export type ArtifactKind = "data_app" | "report" | "slides" | "one_pager";

export type FormatInfo = {
  kind: ArtifactKind;
  label: string;
  short: string;
  placeholder: string;
  build_label: string;
  hint: string;
};

export async function createProject(
  prompt: string,
  goal = "",
  designBrief?: DesignBrief,
  opts?: { useFixture?: boolean; artifactKind?: ArtifactKind | string },
): Promise<Snapshot> {
  return json("/projects", {
    method: "POST",
    body: JSON.stringify({
      prompt,
      goal,
      use_fixture: opts?.useFixture ?? false,
      design_brief: designBrief ?? null,
      artifact_kind: opts?.artifactKind ?? null,
    }),
  });
}

export async function uploadProjectFiles(
  id: string,
  files: File[],
  opts?: { reingest?: boolean },
): Promise<Snapshot & { uploaded?: number; errors?: string[] }> {
  const body = new FormData();
  for (const f of files) body.append("files", f);
  const q = opts?.reingest === false ? "?reingest=false" : "?reingest=true";
  const headers: Record<string, string> = { "X-Tenant-Id": getTenantId() };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}/projects/${id}/upload${q}`, {
    method: "POST",
    body,
    headers,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Upload failed (${res.status})`);
  }
  return res.json();
}

export async function removeProjectSource(id: string, fileName: string): Promise<Snapshot> {
  return json(`/projects/${id}/sources/${encodeURIComponent(fileName)}`, { method: "DELETE" });
}

export async function seedProjectFixtures(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/sources/seed`, { method: "POST" });
}

export async function reingestProjectSources(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/sources/reingest`, { method: "POST" });
}

export async function getProjectSources(id: string): Promise<{
  files: DataRoomFile[];
  fingerprint: string;
  profile?: DataProfile;
  extract?: PlanPreview["extract"];
  row_count?: number;
}> {
  return json(`/projects/${id}/sources`);
}

export async function getProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}`);
}

export async function approveProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/approve`, { method: "POST" });
}

export async function sendChat(id: string, message: string): Promise<Snapshot> {
  return json(`/projects/${id}/chat`, { method: "POST", body: JSON.stringify({ message }) });
}

/** @deprecated Alias of sendChat — main chat is always Prime. */
export async function sendPlanChat(id: string, message: string): Promise<Snapshot> {
  return sendChat(id, message);
}

export async function cancelProjectJob(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/cancel`, { method: "POST" });
}

export async function getProjectJob(id: string): Promise<{ job: JobState; live: boolean }> {
  return json(`/projects/${id}/job`);
}

export async function patchDesignBrief(id: string, designBrief: DesignBrief): Promise<Snapshot> {
  return json(`/projects/${id}/design-brief`, {
    method: "PATCH",
    body: JSON.stringify({ design_brief: designBrief }),
  });
}

export async function rollbackProject(id: string, checkpointId?: string): Promise<Snapshot> {
  return json(`/projects/${id}/rollback`, {
    method: "POST",
    body: JSON.stringify({ checkpoint_id: checkpointId ?? null }),
  });
}

export async function deployProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/deploy`, { method: "POST" });
}

export async function runQuery(id: string, sql: string) {
  return json<{ columns: string[]; rows: Record<string, unknown>[]; row_count: number }>(
    `/projects/${id}/query`,
    { method: "POST", body: JSON.stringify({ sql }) },
  );
}

export async function listEvents(projectId: string): Promise<AgentEvent[]> {
  const data = await json<{ events: AgentEvent[] }>(`/projects/${projectId}/events`);
  return data.events;
}

/** Subscribe to live SSE events. Returns unsubscribe function. */
export function subscribeEvents(projectId: string, onEvent: (evt: AgentEvent) => void): () => void {
  const token = getToken();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  const source = new EventSource(`${API}/projects/${projectId}/events/stream${qs}`);
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as AgentEvent);
    } catch {
      /* ignore malformed */
    }
  };
  return () => source.close();
}

export const DEFAULT_DESIGN_BRIEF: DesignBrief = {
  product_name: "Vendor Risk Command Center",
  one_liner: "Monitor vendor findings and risk scores",
  audience: "internal risk / ops",
  aesthetic: {
    direction: "dense-ops",
    density: "compact",
    color_mode: "dark",
    palette: {
      background: "#0B0F0E",
      surface: "#141A18",
      text: "#E8EEE9",
      accent: "#3D8B6E",
      danger: "#C44B4B",
    },
    typography: { display: "IBM Plex Sans", body: "IBM Plex Sans" },
    shape: "sharp",
    chrome: "no-cards",
    motion: "subtle",
  },
  information_architecture: {
    primary_view: "overview",
    must_have: ["KPI strip", "findings table", "vendor leaderboard"],
    must_not: ["emoji", "purple glow", "generic Inter-on-white"],
  },
  copy_tone: "precise",
  references: [],
  user_notes: "",
};
