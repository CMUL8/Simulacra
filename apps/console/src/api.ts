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

export type Checkpoint = {
  id: string;
  label: string;
  created_at: string;
  current?: boolean;
  raw_label?: string;
  has_files?: boolean | string;
};
export type DataRoomFile = {
  name: string;
  size: number;
  type: string;
  status?: string;
  detail?: string;
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
  source_room?: {
    empty?: boolean;
    row_count?: number;
    file_count?: number;
    file_names?: string[];
    vendors?: string[];
    looks_like_vendor_sample?: boolean;
  };
};

export type ChatThreadSummary = {
  id: string;
  title: string;
  updated_at: string;
  created_at?: string;
  message_count?: number;
  artifact_kind?: string | null;
  artifact_mode?: "shared" | "own" | string;
  active?: boolean;
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
  active_chat_id?: string;
  chats?: ChatThreadSummary[];
  chat_index?: ChatThreadSummary[];
  app_config: AppConfig;
  row_count: number;
  checkpoints: Checkpoint[];
  active_checkpoint: number;
  plan_preview: PlanPreview;
  design_brief?: DesignBrief;
  /** Legacy-shell execution status. Workplace views use durable Mission projections. */
  job?: JobState;
  created_at?: string;
};

export type Snapshot = {
  project: Project;
  preview_data: { columns: string[]; rows: Record<string, unknown>[]; row_count: number };
  preview_url: string | null;
  /** Legacy-shell execution status. */
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
  avatar_url?: string | null;
};

export type AuthSession = {
  token: string;
  token_type: string;
  user: AuthUser;
  tenants: Tenant[];
  tenant_id: string;
};

export type WorkplaceFlags = {
  workplace_shell_v1: boolean;
  workplace_attention_v1: boolean;
  workplace_conversation_v1: boolean;
  workplace_files_v1: boolean;
  workplace_preview_origin_v1: boolean;
  workplace_sse_v1: boolean;
  workplace_bootstrap_v1: boolean;
};

export type MissionSummary = {
  id: string;
  title: string;
  outcome_summary: string;
  public_state: string;
  updated_at: string;
  human_count: number;
  agent_count: number;
  active_work_count: number;
  needs_human_count: number;
  verified_output_count: number;
  current_human_permissions: string[];
};

export type AttentionItem = {
  id: string;
  mission_id: string;
  type: string;
  title: string;
  summary: string;
  source_event_id: string;
  subject_id: string;
  priority: number;
  actionable: boolean;
  read: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
  deep_link: string;
  allowed_actions: string[];
};

export type WorkBucket = "needs_you" | "in_progress" | "ready_for_review" | "done" | "stopped";
export type WorkView = "list" | "board";

export type WorkAssignee = {
  id: string;
  display_name: string;
  kind: "human" | "agent";
  avatar_url: string | null;
};

export type WorkActionTarget = {
  kind: "task" | "approval" | "output" | "run" | "plan";
  id: string;
  revision: number;
  run_revision?: number;
  next_states?: string[];
  file_id?: string;
};

export type WorkItem = {
  source_type: string;
  source_id: string;
  mission_id: string;
  revision: number;
  title: string;
  summary: string;
  state: WorkBucket;
  assignee: WorkAssignee | null;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  action_targets: Record<string, WorkActionTarget>;
};

export type WorkViewFilters = {
  bucket?: WorkBucket;
  mission_id?: string;
  assignee_id?: string;
};

export type WorkViewPreference = {
  scope: string;
  view: WorkView;
  filters: WorkViewFilters;
  revision: number;
  updated_at: string | null;
};

export type NotificationPreference = {
  event_selection: string;
  channels: string[];
  digest: string;
  muted_mission_ids: string[];
  revision: number;
  updated_at: string | null;
};

export type WorkplacePreferences = {
  work_view_preferences: WorkViewPreference[];
  notification_preference: NotificationPreference;
};

export type MissionFileItem = {
  id: string;
  mission_id: string;
  kind: "source" | "output" | "evidence";
  name: string;
  media_type: string;
  size: number;
  sha256: string;
  state: string;
  version: number;
  parent_output_id?: string | null;
  run_id?: string | null;
  producer_id: string | null;
  producer: { id: string; display_name?: string } | null;
  verifier: { id: string; display_name?: string } | null;
  source_ids: string[];
  introduced_by_message_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  previewable: boolean;
  downloadable: boolean;
  allowed_actions?: string[];
  action_targets?: Record<string, WorkActionTarget>;
};

export type PreviewSession = {
  previewOrigin: string;
  previewUrl: string;
};

export type ConversationAuthor = {
  id: string;
  kind: "human" | "agent" | "system";
  display_name: string;
  avatar_url: string | null;
};

export type ConversationMessage = {
  id: string;
  mission_id: string;
  kind: string;
  author: ConversationAuthor;
  body: string | null;
  created_at: string;
  edited_at: string | null;
  thread: { reply_count: number; latest_replies: ConversationMessage[] };
  reactions: Array<{
    reaction: "acknowledge" | "check" | "question" | "celebrate";
    count: number;
    reacted: boolean;
  }>;
  saved: boolean;
  links: { work_item_id: string | null; run_id: string | null; output_id: string | null };
};

export type ConversationWorkItem = {
  id: string;
  title: string;
  state: string;
  assignee_agent_ids?: string[];
  reviewer_human_ids?: string[];
  allowed_actions: string[];
  [key: string]: unknown;
};

export type ConversationPage = {
  items: ConversationMessage[];
  next_before: string | null;
};

export type ConversationReplyPage = ConversationPage;

export type ConversationSendRequest = {
  client_request_id: string;
  body: string;
  mode: "message" | "assignment";
  assignee_agent_ids: string[];
  reviewer_human_ids: string[];
  source_message_id: null;
};

export type ConversationSendResponse = {
  message: ConversationMessage;
  work_item: ConversationWorkItem | null;
};

export type ConversationReplyRequest = {
  client_request_id: string;
  body: string;
};

export type ConversationMutationRequest = {
  client_request_id: string;
};

export type WorkspaceWakeUp = {
  id: string;
  type: string;
  mission_id: string;
  occurred_at: string;
};

export type WorkspaceEventStreamOptions = {
  lastEventId: string | null;
  signal: AbortSignal;
  onOpen: () => void;
  onWakeUp: (event: WorkspaceWakeUp) => void;
};

export type WorkspaceEventStream = (options: WorkspaceEventStreamOptions) => Promise<void>;

export function getTenantId(): string {
  // Empty until login/me sets a real workspace — never invent "default".
  // Sending X-Tenant-Id: default 403s users who only belong to another tenant
  // and the console clears the session → landing with zero project cards.
  return localStorage.getItem(TENANT_KEY) || "";
}

export function setTenantId(id: string) {
  const tid = (id || "").trim();
  if (!tid) localStorage.removeItem(TENANT_KEY);
  else localStorage.setItem(TENANT_KEY, tid);
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

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, message: string, code = "request_failed") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) || {}),
  };
  const tid = getTenantId();
  if (tid) headers["X-Tenant-Id"] = tid;
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    const raw = await res.text();
    let message = raw || res.statusText;
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown; message?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) message = parsed.detail;
      else if (parsed.detail && typeof parsed.detail === "object" && "message" in parsed.detail
        && typeof parsed.detail.message === "string" && parsed.detail.message.trim()) message = parsed.detail.message;
      else if (typeof parsed.message === "string" && parsed.message.trim()) message = parsed.message;
    } catch {
      /* Non-JSON errors already have the most useful available message. */
    }
	const friendly = message
	  .replace(/(?:Operation\s+Graph\s+)?revision is not approved exactly:\s*[0-9a-f]{64}/i, "Review and approve the current Mission plan before starting this Mission.")
	  .replace(/(?:An\s+)?exactly approved(?:\s+Operation\s+Graph)? .+ revision is required before building/i, "Review and approve the current Mission plan before building.")
	  .replace(/project room owner or admin required/i, "Only a Mission owner or admin can do that.");
    throw new ApiError(res.status, friendly);
  }
  if (res.status === 204) return undefined as T;
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
  _tenantName?: string,
): Promise<AuthSession> {
  const data = await json<AuthSession>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, name }),
  });
  setToken(data.token);
  if (data.tenant_id) setTenantId(data.tenant_id);
  return data;
}

export async function forgotPassword(
  email: string,
): Promise<{ ok: boolean; reset_url?: string; token?: string; expires_in_minutes?: number }> {
  return json("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function resetPassword(token: string, password: string): Promise<{ ok: boolean; email: string }> {
  return json("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });
}

export async function fetchMe(): Promise<{
  user: AuthUser;
  tenant_id: string;
  role: string;
  tenants: Tenant[];
  workplace_flags?: WorkplaceFlags;
}> {
  return json("/auth/me");
}

export async function listMissionSummaries(state: "active" | "all", cursor?: string | null): Promise<{
  items: MissionSummary[];
  next_cursor: string | null;
}> {
  const params = new URLSearchParams({ state });
  if (cursor) params.set("cursor", cursor);
  return json(`/missions?${params.toString()}`);
}

export async function listWorkspaceAttention(filter: "actionable" | "all", cursor?: string | null): Promise<{
  items: AttentionItem[];
  next_cursor: string | null;
  unread_count: number;
  actionable_count: number;
}> {
  const params = new URLSearchParams({ filter });
  if (cursor) params.set("cursor", cursor);
  return json(`/workspace/attention?${params.toString()}`);
}

export async function markWorkspaceAttentionRead(eventId: string, expectedRevision: number): Promise<{ item: AttentionItem }> {
  return json("/workspace/attention/read", {
    method: "POST",
    body: JSON.stringify({ event_id: eventId, expected_revision: expectedRevision }),
  });
}

export async function listWorkspaceWork(filters: WorkViewFilters = {}, cursor?: string | null): Promise<{
  items: WorkItem[];
  next_cursor: string | null;
}> {
  const params = new URLSearchParams({ limit: "50" });
  if (filters.bucket) params.set("bucket", filters.bucket);
  if (filters.mission_id) params.set("mission_id", filters.mission_id);
  if (filters.assignee_id) params.set("assignee_id", filters.assignee_id);
  if (cursor) params.set("cursor", cursor);
  return json(`/workspace/work?${params.toString()}`);
}

export async function getWorkspacePreferences(): Promise<WorkplacePreferences> {
  return json("/workspace/preferences");
}

export async function putNotificationPreference(body: {
  expected_revision: number;
  event_selection: string;
  channels: string[];
  digest: string;
  muted_mission_ids: string[];
}): Promise<{ notification_preference: NotificationPreference }> {
  return json("/workspace/preferences/notifications", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function putWorkViewPreference(body: {
  expected_revision: number;
  scope: string;
  view: WorkView;
  filters: WorkViewFilters;
}): Promise<{ work_view_preference: WorkViewPreference }> {
  return json("/workspace/preferences/work-view", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function listMissionFiles(projectId: string, kind: "all" | MissionFileItem["kind"] = "all"): Promise<{
  items: MissionFileItem[];
}> {
  const params = new URLSearchParams({ kind });
  return json(`/projects/${encodeURIComponent(projectId)}/files?${params.toString()}`);
}

export function missionFileContentUrl(projectId: string, fileId: string, disposition: "inline" | "attachment"): string {
  return `${API}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileId)}/content?disposition=${disposition}`;
}

export async function fetchMissionFileContent(projectId: string, fileId: string, disposition: "inline" | "attachment" = "inline"): Promise<Blob> {
  const headers: Record<string, string> = {};
  const tenantId = getTenantId();
  const token = getToken();
  if (tenantId) headers["X-Tenant-Id"] = tenantId;
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(missionFileContentUrl(projectId, fileId, disposition), { headers, cache: "no-store" });
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) throw new ApiError(response.status, "You no longer have access to this Mission.");
    throw new ApiError(response.status, disposition === "inline" ? "This file cannot be previewed right now." : "This file cannot be downloaded right now.");
  }
  return response.blob();
}

export async function exchangeMissionPreview(projectId: string, signal?: AbortSignal): Promise<PreviewSession> {
  const exchange = await json<{ exchange_id: string; exchange_proof: string; preview_origin: string }>(
    `/projects/${encodeURIComponent(projectId)}/preview/exchanges`,
    { method: "POST", body: "{}", signal },
  );
  const previewOrigin = new URL(exchange.preview_origin).origin;
  const response = await fetch(`${previewOrigin}/preview/exchange`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ exchange_id: exchange.exchange_id, exchange_proof: exchange.exchange_proof }),
    signal,
  });
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) throw new ApiError(response.status, "You no longer have access to this Mission.");
    throw new ApiError(response.status, "The verified preview is temporarily unavailable.");
  }
  return {
    previewOrigin,
    previewUrl: `${previewOrigin}/projects/${encodeURIComponent(projectId)}/preview/`,
  };
}

export async function getMissionConversation(projectId: string, before?: string | null, limit = 50): Promise<ConversationPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return json(`/projects/${encodeURIComponent(projectId)}/conversation?${params.toString()}`);
}

export async function postMissionConversationMessage(projectId: string, body: ConversationSendRequest): Promise<ConversationSendResponse> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function patchMissionConversationMessage(projectId: string, messageId: string, body: Record<string, unknown>): Promise<{ message: ConversationMessage }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deleteMissionConversationMessage(projectId: string, messageId: string, body: Record<string, unknown>): Promise<{ message: ConversationMessage }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}`, {
    method: "DELETE",
    body: JSON.stringify(body),
  });
}

export async function postMissionConversationReply(
  projectId: string,
  messageId: string,
  body: ConversationReplyRequest,
): Promise<{ message: ConversationMessage }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}/replies`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getMissionConversationReplies(
  projectId: string,
  messageId: string,
  before?: string | null,
  limit = 50,
): Promise<ConversationReplyPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}/replies?${params.toString()}`);
}

export async function putMissionConversationReaction(
  projectId: string,
  messageId: string,
  reaction: ConversationMessage["reactions"][number]["reaction"],
  body: ConversationMutationRequest,
): Promise<{ message: ConversationMessage }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}/reactions/${encodeURIComponent(reaction)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteMissionConversationReaction(
  projectId: string,
  messageId: string,
  reaction: ConversationMessage["reactions"][number]["reaction"],
  body: ConversationMutationRequest,
): Promise<{ message: ConversationMessage }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}/reactions/${encodeURIComponent(reaction)}`, {
    method: "DELETE",
    body: JSON.stringify(body),
  });
}

export async function putMissionConversationSaved(
  projectId: string,
  messageId: string,
  body: ConversationMutationRequest,
): Promise<{ saved: boolean }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}/saved`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteMissionConversationSaved(
  projectId: string,
  messageId: string,
  body: ConversationMutationRequest,
): Promise<{ saved: boolean }> {
  return json(`/projects/${encodeURIComponent(projectId)}/conversation/messages/${encodeURIComponent(messageId)}/saved`, {
    method: "DELETE",
    body: JSON.stringify(body),
  });
}

function authenticatedStreamHeaders(lastEventId: string | null): Headers {
  const headers = new Headers({ Accept: "text/event-stream" });
  const tenantId = getTenantId();
  const token = getToken();
  if (tenantId) headers.set("X-Tenant-Id", tenantId);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);
  return headers;
}

function dispatchSseBlock(block: string, onWakeUp: (event: WorkspaceWakeUp) => void): void {
  let eventId = "";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("id:")) eventId = line.slice(3).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return;
  try {
    const value = JSON.parse(data.join("\n")) as Partial<WorkspaceWakeUp>;
    const id = typeof value.id === "string" && value.id ? value.id : eventId;
    if (!id || typeof value.type !== "string" || typeof value.mission_id !== "string" || typeof value.occurred_at !== "string") return;
    onWakeUp({ id, type: value.type, mission_id: value.mission_id, occurred_at: value.occurred_at });
  } catch {
    /* A malformed wake-up is ignored; durable reads remain authoritative. */
  }
}

export const openWorkspaceEventStream: WorkspaceEventStream = async ({ lastEventId, signal, onOpen, onWakeUp }) => {
  const response = await fetch(`${API}/workspace/events`, {
    method: "GET",
    headers: authenticatedStreamHeaders(lastEventId),
    cache: "no-store",
    signal,
  });
  if (!response.ok || !response.body) throw new ApiError(response.status, "Live updates are temporarily unavailable.");
  onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      blocks.forEach((block) => dispatchSseBlock(block, onWakeUp));
    }
  } finally {
    reader.releaseLock();
  }
  if (!signal.aborted) throw new ApiError(503, "Live updates are temporarily unavailable.");
};

export async function listProjects(): Promise<Project[]> {
  const data = await json<{ projects: Project[] }>("/projects");
  return data.projects;
}

export type MissionAgent = Record<string, unknown> & {
  id: string; name: string; role: string; mandate: string; scope: "sources" | "documents" | "app"; autonomy: string;
};
export type MissionRunSummary = {
  id: string; mission_id?: string; status: string; assigned_agent_ids?: string[];
  completed_agent_ids?: string[]; current_agent_id?: string | null;
  trigger_snapshot?: { type?: string; note?: string };
  error?: { code: string; message: string } | null;
  active_approval_id?: string | null; revision: number; started_at?: string | null;
  completed_at?: string | null; created_at?: string; updated_at?: string;
};
export type MissionEvent = {
  id: string; run_id: string; type: string; timestamp: string;
  payload: Record<string, unknown>;
};
export type MissionApproval = Record<string, unknown> & {
  id: string; run_id: string; agent_id?: string; status: string; revision: number;
  created_at?: string; updated_at?: string;
};
export type MissionDeliverable = {
  id: string; mission_id?: string; type?: string; name: string; producer_id: string;
  version: number; state: string; verified_by?: string | null;
  verified_at?: string | null; supersedes_id?: string | null;
  created_at?: string; updated_at?: string;
};
export type MissionOverview = {
  mission: Record<string, unknown> | null;
  agents: MissionAgent[];
  runs: MissionRunSummary[];
  triggers: Record<string, unknown>[];
	deliverables: MissionDeliverable[];
  events: MissionEvent[];
  approvals: MissionApproval[];
	crew_recommendations?: MissionAgentRecommendation[];
  readiness: {
    graph: {
      status: "missing" | "pending_approval" | "approved" | "invalid";
      revision: number | null;
    };
    crew_count: number;
  };
};
export type MissionAgentInput = { name: string; role: string; mandate: string; scope: "sources" | "documents" | "app"; autonomy: "assist" | "execute_safely" | "operate_with_checkpoints" };
export type MissionAgentRecommendation = MissionAgentInput & { slug?: string; rationale?: string };
export type MissionTriggerInput = { type: "manual" | "cron" | "condition"; cron?: string; condition?: { fact: string; operator: string; value: string | number | boolean }; timezone?: string; concurrency_policy?: "queue" | "skip" | "replace" | "merge"; enabled?: boolean };

export async function getMission(projectId: string): Promise<MissionOverview> {
  return json(`/projects/${projectId}/mission`);
}

export async function bootstrapMission(projectId: string, body: Record<string, unknown>) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission`, { method: "POST", body: JSON.stringify(body) });
}

export async function updateMission(projectId: string, body: Record<string, unknown>) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function createMissionAgent(projectId: string, body: MissionAgentInput) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission/agents`, { method: "POST", body: JSON.stringify(body) });
}

export async function createMissionTrigger(projectId: string, body: MissionTriggerInput) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission/automation`, { method: "POST", body: JSON.stringify(body) });
}

export async function verifyMissionDeliverable(projectId: string, deliverableId: string, expectedVersion: number) {
  return json<MissionDeliverable>(`/projects/${projectId}/mission/deliverables/${deliverableId}/verify`, { method: "POST", body: JSON.stringify({ decision: "verify", expected_version: expectedVersion }) });
}

export async function createMissionRun(projectId: string, triggerNote = "", agentIds: string[] = []) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission/runs`, { method: "POST", body: JSON.stringify({ trigger_note: triggerNote, agent_ids: agentIds }) });
}
export async function retryMissionRun(projectId: string, runId: string, expectedRevision: number) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission/runs/${encodeURIComponent(runId)}/retry`, { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}
export async function cancelMissionRun(projectId: string, runId: string, expectedRevision: number) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}
export async function decideMissionCheckpoint(projectId: string, approvalId: string, decision: "approve" | "reject", expectedRevision: number, expectedRunRevision: number) {
  return json<Record<string, unknown>>(`/projects/${projectId}/mission/approvals/${encodeURIComponent(approvalId)}`, { method: "POST", body: JSON.stringify({ decision, expected_revision: expectedRevision, expected_run_revision: expectedRunRevision }) });
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

export type StagedMissionSource = {
  source_ref: string;
  sha256: string;
  filename: string;
  media_type: string;
};

export type MissionBootstrapRequest = {
  client_request_id: string;
  prompt: string;
  goal: string;
  design_brief: DesignBrief | null;
  artifact_kind: ArtifactKind;
  staged_source_refs: string[];
};

export type MissionBootstrapPending = {
  status: "PREPARED" | "COMMIT_DECIDED" | "STORES_DURABLE" | "provisioning";
  transaction_id: string;
  project_id: string;
  provisioning: true;
  retry_after_seconds: number;
};

export type MissionBootstrapComplete = {
  status: "COMPLETE";
  transaction_id: string;
  project: { id: string; [key: string]: unknown };
  project_id?: string;
  provisioning: false;
  [key: string]: unknown;
};

export type MissionBootstrapAborted = { status: "ABORTED"; code: "bootstrap_aborted" };
export type MissionBootstrapResult = MissionBootstrapPending | MissionBootstrapComplete | MissionBootstrapAborted;

const bootstrapErrorCopy: Record<string, string> = {
  idempotency_mismatch: "This Mission draft changed after creation began. Discard it and start a new Mission.",
  bootstrap_aborted: "This Mission could not be created safely. Start a new Mission to try again.",
  bootstrap_unavailable: "This Mission setup is no longer available. Return to Missions and start again.",
  source_stage_failed: "One source could not be added. Try that source again before creating the Mission.",
};

async function bootstrapResponse<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const tid = getTenantId();
  if (tid) headers.set("X-Tenant-Id", tid);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, { ...init, headers });
  const body = await response.json().catch(() => null) as { code?: unknown; message?: unknown; detail?: unknown } | null;
  if (!response.ok) {
    const code = typeof body?.code === "string"
      ? body.code
      : body?.detail && typeof body.detail === "object" && "code" in body.detail && typeof body.detail.code === "string"
        ? body.detail.code
        : "request_failed";
    const fallback = response.status === 401 || response.status === 403
      ? "Sign in again, then continue creating this Mission."
      : response.status >= 500
        ? "Missions could not finish that step. Try again."
        : "Missions could not complete that request. Review the details and try again.";
    throw new ApiError(response.status, bootstrapErrorCopy[code] || fallback, code);
  }
  return body as T;
}

export async function stageMissionSource(file: File, clientRequestId: string): Promise<StagedMissionSource> {
  const body = new FormData();
  body.append("file", file);
  body.append("client_request_id", clientRequestId);
  return bootstrapResponse("/workspace/bootstrap/sources", { method: "POST", body });
}

export async function createWorkplaceMission(body: MissionBootstrapRequest): Promise<MissionBootstrapResult> {
  return bootstrapResponse("/projects", { method: "POST", body: JSON.stringify(body) });
}

export async function getWorkplaceMissionBootstrap(transactionId: string): Promise<MissionBootstrapResult> {
  return bootstrapResponse(`/projects/bootstrap/${encodeURIComponent(transactionId)}`);
}

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
  opts?: { artifactKind?: ArtifactKind | string },
): Promise<Snapshot> {
  return json("/projects", {
    method: "POST",
    body: JSON.stringify({
      prompt,
      goal,
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
  const headers: Record<string, string> = {};
  const tid = getTenantId();
  if (tid) headers["X-Tenant-Id"] = tid;
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

export async function reingestProjectSources(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/sources/reingest`, { method: "POST" });
}

export async function getProjectSources(id: string): Promise<{
  files: DataRoomFile[];
  profile?: DataProfile;
  extract?: PlanPreview["extract"];
  row_count?: number;
}> {
  return json(`/projects/${id}/sources`);
}

export async function getProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}`);
}

export type Cmul8MemberRecord = {
  actor_id: string;
  role: string;
  display_name?: string;
  actor_type?: "human" | "agent";
  joined_at?: string;
};

export type Cmul8InvitationSummary = {
  id: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  revision: number;
};

export type Cmul8TaskRecord = {
  id: string;
  title: string;
  objective?: string;
  state: "proposed" | "ready" | "working" | "in_review" | "done" | "blocked" | "failed" | "cancelled" | string;
  owner_id?: string | null;
  collaborator_ids?: string[];
  acceptance_criteria?: string[];
  revision: number;
  created_at?: string;
  updated_at?: string;
};

export type Cmul8ReviewRecord = {
  id: string;
  task_id: string;
  author_id: string;
  author_name?: string;
  role?: string;
  decision: "approve" | "request_changes" | "question" | "reject" | "rollback" | string;
  comment?: string;
  task_revision?: number;
  created_at: string;
  updated_at?: string;
};

export type Cmul8CommentRecord = {
  id: string;
  author_id: string;
  body: string;
  status: "posted" | string;
  plan_revision?: number | null;
  created_at: string;
  updated_at?: string;
};

export type Cmul8DomainEventRecord = {
  id: string;
  actor_type: string;
  actor_id: string;
  task_id?: string | null;
  action: string;
  result: string;
  timestamp: string;
  reviewer_role?: string;
};

export type Cmul8RoomPayload = {
  room: { id: string; members: Cmul8MemberRecord[]; revision: number; created_at?: string; updated_at?: string };
  project: { id: string; name: string; objective: string };
  tasks: Cmul8TaskRecord[];
  comments: Cmul8CommentRecord[];
  reviews: Cmul8ReviewRecord[];
  events: Cmul8DomainEventRecord[];
  mission_plan?: { revision: number; objective: string; steps: string[]; human_checkpoints: string[]; status: "pending_approval" | "approved" } | null;
  away: { since?: string | null; total: number; unread: number; counts: Record<string, number>; highlights: Array<{ position: number; category: string; unread: boolean; event: Cmul8DomainEventRecord; deep_link: Record<string, string | null> }> };
  permissions: { manage_tasks: boolean; review_tasks: boolean; review_graph: boolean; invite: boolean; comment: boolean };
  presence: Array<{ actor_id: string; status: "online" | "away" | "offline"; last_seen_at: string | null }>;
};

export async function getCmul8Room(projectId: string): Promise<Cmul8RoomPayload> {
  return json(`/projects/${projectId}/cmul8/room`);
}

export async function createCmul8Room(projectId: string): Promise<Cmul8RoomPayload> {
  return json(`/projects/${projectId}/cmul8/room`, { method: "POST", body: "{}" });
}

export async function addCmul8RoomMember(projectId: string, member: { member_id?: string; member_email?: string; role: "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver"; expected_revision: number }): Promise<Cmul8RoomPayload["room"]> {
  return json(`/projects/${projectId}/cmul8/room/members`, { method: "POST", body: JSON.stringify(member) });
}

export async function createCmul8Invitation(projectId: string, body: { client_request_id: string; email: string; role: "owner" | "admin" | "member" | "viewer" | "reviewer" | "approver" }): Promise<{ invitation: Cmul8InvitationSummary & { expires_at: string }; token: string }> {
  return json(`/projects/${projectId}/cmul8/room/invitations`, { method: "POST", body: JSON.stringify(body) });
}

export async function acceptCmul8Invitation(projectId: string, invitationId: string, body: { client_request_id: string; token: string }): Promise<{ invitation: Cmul8InvitationSummary; membership: Pick<Cmul8MemberRecord, "actor_id" | "role"> }> {
  return json(`/projects/${projectId}/cmul8/room/invitations/${encodeURIComponent(invitationId)}/accept`, { method: "POST", body: JSON.stringify(body) });
}

export async function revokeCmul8Invitation(projectId: string, invitationId: string, body: { client_request_id: string; expected_revision: number }): Promise<{ invitation: Cmul8InvitationSummary }> {
  return json(`/projects/${projectId}/cmul8/room/invitations/${encodeURIComponent(invitationId)}/revoke`, { method: "POST", body: JSON.stringify(body) });
}

export async function removeCmul8RoomMember(projectId: string, actorId: string, body: { client_request_id: string; expected_room_revision: number }): Promise<Cmul8RoomPayload["room"]> {
  return json(`/projects/${projectId}/cmul8/room/members/${encodeURIComponent(actorId)}/remove`, { method: "POST", body: JSON.stringify(body) });
}

export async function heartbeatCmul8Presence(projectId: string): Promise<{ presence: Cmul8RoomPayload["presence"][number] }> {
  return json(`/projects/${projectId}/cmul8/room/presence`, { method: "POST" });
}

export async function markCmul8InboxRead(projectId: string, eventId?: string): Promise<{ last_read_position: number; updated_at: string }> {
  const query = eventId ? `?event_id=${encodeURIComponent(eventId)}` : "";
  return json(`/projects/${projectId}/cmul8/inbox/read${query}`, { method: "POST" });
}

export async function createCmul8Task(projectId: string, task: { title: string; objective: string; acceptance_criteria: string[]; owner_id?: string }): Promise<Cmul8TaskRecord> {
  return json(`/projects/${projectId}/cmul8/tasks`, { method: "POST", body: JSON.stringify(task) });
}

export async function transitionCmul8Task(projectId: string, taskId: string, state: string, expectedRevision: number): Promise<void> {
  await json(`/projects/${projectId}/cmul8/tasks/${encodeURIComponent(taskId)}/transition`, {
    method: "POST", body: JSON.stringify({ state, expected_revision: expectedRevision }),
  });
}

export async function claimCmul8Task(projectId: string, taskId: string, expectedRevision: number): Promise<Cmul8TaskRecord> {
  return json(`/projects/${projectId}/cmul8/tasks/${encodeURIComponent(taskId)}/claim?expected_revision=${expectedRevision}`, { method: "POST" });
}

export async function reviewCmul8Task(projectId: string, taskId: string, decision: string, body: string, expectedRevision: number): Promise<void> {
  await json(`/projects/${projectId}/cmul8/tasks/${encodeURIComponent(taskId)}/reviews`, {
    method: "POST", body: JSON.stringify({ decision, note: body, expected_revision: expectedRevision }),
  });
}

export async function addCmul8Comment(projectId: string, body: { body: string; plan_revision?: number }): Promise<Cmul8CommentRecord> {
  return json(`/projects/${projectId}/cmul8/comments`, { method: "POST", body: JSON.stringify(body) });
}

export async function approveCurrentMissionPlan(projectId: string, expectedRevision: number): Promise<void> {
  await json(`/projects/${projectId}/cmul8/operation-graph/current/approve`, { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}

/** Legacy project-shell approval. The workplace shell does not call this route. */
export async function approveProject(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/approve`, { method: "POST" });
}

export async function sendChat(id: string, message: string, chatId?: string | null): Promise<Snapshot> {
  return json(`/projects/${id}/chat`, {
    method: "POST",
    body: JSON.stringify({ message, chat_id: chatId || undefined }),
  });
}

/** @deprecated Alias of sendChat — main chat is always Prime. */
export async function sendPlanChat(id: string, message: string): Promise<Snapshot> {
  return sendChat(id, message);
}

export async function createChat(
  projectId: string,
  opts?: { title?: string; prompt?: string; artifact_kind?: string; artifact_mode?: "shared" | "own" },
): Promise<Snapshot> {
  return json(`/projects/${projectId}/chats`, {
    method: "POST",
    body: JSON.stringify({
      title: opts?.title,
      prompt: opts?.prompt || "",
      artifact_kind: opts?.artifact_kind,
      artifact_mode: opts?.artifact_mode || "shared",
    }),
  });
}

export async function activateChat(projectId: string, chatId: string): Promise<Snapshot> {
  return json(`/projects/${projectId}/chats/activate`, {
    method: "POST",
    body: JSON.stringify({ chat_id: chatId }),
  });
}

export async function deleteChat(projectId: string, chatId: string): Promise<Snapshot> {
  return json(`/projects/${projectId}/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
}

export async function cancelProjectJob(id: string): Promise<Snapshot> {
  return json(`/projects/${id}/cancel`, { method: "POST" });
}

/** Legacy project-shell liveness check. The workplace shell does not call this route. */
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

export async function fetchCmul8Observability<T = unknown>(projectId: string): Promise<T> {
  return json<T>(`/projects/${encodeURIComponent(projectId)}/cmul8/observability`);
}

export async function fetchCmul8ObservabilityDetail<T = unknown>(projectId: string, kind: string, entityId: string): Promise<T> {
  return json<T>(`/projects/${encodeURIComponent(projectId)}/cmul8/observability/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}`);
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
  product_name: "Untitled",
  one_liner: "From your sources",
  audience: "internal stakeholders",
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
    must_have: ["KPI strip", "primary table", "clear hierarchy"],
    must_not: ["emoji", "purple glow", "generic Inter-on-white"],
  },
  copy_tone: "precise",
  references: [],
  user_notes: "",
};
