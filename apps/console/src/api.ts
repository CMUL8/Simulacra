const API = "/api";

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
  allowed_models?: string[];
  retention_days?: number;
  require_approve?: boolean;
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
export type PlanPreview = {
  row_count: number;
  high_risk: number;
  vendors: string[];
  files: DataRoomFile[];
  summary: string;
  sample_rows: Record<string, unknown>[];
};
export type Project = {
  id: string;
  prompt: string;
  goal: string;
  tenant_id?: string;
  phase: "plan" | "build" | "ready";
  plan_approved: boolean;
  status: string;
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

export type DataRoomFile = { name: string; size: number; type: string };

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

export function getTenantId(): string {
  return localStorage.getItem(TENANT_KEY) || "default";
}

export function setTenantId(id: string) {
  localStorage.setItem(TENANT_KEY, id);
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Tenant-Id": getTenantId(),
    ...((init?.headers as Record<string, string>) || {}),
  };
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

export async function createProject(
  prompt: string,
  goal = "",
  designBrief?: DesignBrief,
): Promise<Snapshot> {
  return json("/projects", {
    method: "POST",
    body: JSON.stringify({ prompt, goal, use_fixture: true, design_brief: designBrief ?? null }),
  });
}

export async function getProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}`);
}

export async function sendPlanChat(id: string, message: string): Promise<Snapshot> {
  return json(`/projects/${id}/plan`, { method: "POST", body: JSON.stringify({ message }) });
}

export async function approveProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/approve`, { method: "POST" });
}

export async function sendChat(id: string, message: string): Promise<Snapshot> {
  return json(`/projects/${id}/chat`, { method: "POST", body: JSON.stringify({ message }) });
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
  const source = new EventSource(`${API}/projects/${projectId}/events/stream`);
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
