// API client for the VoxDesk FastAPI backend.
//
// * Every request carries the access token as a Bearer header and sends
//   credentials so the HttpOnly refresh cookie flows with it.
// * A 401 triggers a single refresh-and-retry (POST /auth/refresh), then a
//   redirect to /login only if the refresh also fails.
// * Error bodies use FastAPI's `detail` shape, both the plain string form and
//   the 422 validation-list form.

import { clearToken, getToken, setToken } from "./auth";
import type {
  AgentConfig,
  AppointmentListResponse,
  AuditEntry,
  AvailabilityResponse,
  BillingStatus,
  CallDetail,
  CallListResponse,
  Campaign,
  CampaignResults,
  DocumentListResponse,
  IntegrationListResponse,
  Invoice,
  KnowledgeStats,
  Lead,
  LlmPreset,
  LoginResult,
  MFAChallengeResponse,
  MeResponse,
  OverviewResponse,
  Plan,
  ProviderCatalogueResponse,
  SearchResponse,
  SyncListResponse,
  TokenResponse,
  TranscriptTurn,
  UsageResponse,
  User,
} from "./types";

const RAW_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
export const BASE_URL = RAW_BASE_URL.replace(/\/+$/, "");

export class ApiError extends Error {
  status: number;

  /**
   * The machine-readable refusals the identity API adds to its error body:
   * `code` names the class of refusal (`policy_denied`, `owner_required`,
   * `reauth_required`, `identity_conflict`) and `reason` names the specific
   * rule inside it (`emergency_disabled`, `address_belongs_to_another_tenant`).
   *
   * A UI branches on these, never on the prose — the message is for the person
   * reading it and may be reworded.
   */
  code: string;
  reason: string;

  constructor(
    status: number,
    message: string,
    code = "",
    reason = "",
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.reason = reason;
  }
}

interface ErrorShape {
  message: string;
  code: string;
  reason: string;
}

/**
 * Read an error body.
 *
 * Three shapes reach this function: FastAPI's plain string `detail`, its 422
 * validation list, and this product's identity error object
 * `{code, message, reason}`. All three are read here so a page can show what
 * the server actually said.
 */
function errorFromBody(body: unknown): ErrorShape {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.detail;
    if (typeof detail === "string") {
      return { message: detail, code: "", reason: "" };
    }
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: unknown } | undefined;
      if (first && typeof first.msg === "string") {
        return { message: first.msg, code: "", reason: "" };
      }
    }
    if (detail && typeof detail === "object") {
      const inner = detail as Record<string, unknown>;
      const message =
        typeof inner.message === "string"
          ? inner.message
          : typeof inner.code === "string"
            ? inner.code
            : "request failed";
      return {
        message,
        code: typeof inner.code === "string" ? inner.code : "",
        reason: typeof inner.reason === "string" ? inner.reason : "",
      };
    }
    if (typeof record.message === "string") {
      return {
        message: record.message,
        code: typeof record.code === "string" ? record.code : "",
        reason: typeof record.reason === "string" ? record.reason : "",
      };
    }
  }
  return { message: "request failed", code: "", reason: "" };
}

let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(`${BASE_URL}/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return false;
        const body = (await res.json()) as TokenResponse;
        setToken(body.access_token);
        return true;
      } catch {
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

interface RequestOptions {
  /** When false, a 401 is reported as-is instead of attempting a refresh. */
  retryOn401?: boolean;
}

/**
 * Perform a request and keep the status code.
 *
 * Most callers want the body and nothing else, and use `request` below. A few
 * endpoints carry meaning in the status itself — `/auth/login` answers **202**
 * with a second-factor challenge instead of tokens — and they need both.
 */
export async function perform<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<{ status: number; body: T }> {
  const { retryOn401 = true } = options;
  const token = getToken();

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (res.status === 401 && retryOn401) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      return perform<T>(path, init, { retryOn401: false });
    }
    clearToken();
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    throw new ApiError(401, "session expired");
  }

  if (!res.ok) {
    let failure: ErrorShape = {
      message: `HTTP ${res.status}`,
      code: "",
      reason: "",
    };
    try {
      failure = errorFromBody(await res.json());
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    throw new ApiError(
      res.status,
      failure.message,
      failure.code,
      failure.reason,
    );
  }

  if (res.status === 204) return { status: res.status, body: undefined as T };
  return { status: res.status, body: (await res.json()) as T };
}

/** The body of a successful request, without the status code. */
export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  return (await perform<T>(path, init, options)).body;
}

/**
 * The body of a successful request as text.
 *
 * Translation exports answer with SRT or plain text rather than JSON, so they
 * cannot go through `request`, which parses every body as JSON.
 */
export async function requestText(
  path: string,
  init: RequestInit = {},
): Promise<string> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (res.status === 401) {
    const refreshed = await tryRefresh();
    if (refreshed) return requestText(path, init);
    clearToken();
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    throw new ApiError(401, "session expired");
  }
  if (!res.ok) {
    let failure: ErrorShape = { message: `HTTP ${res.status}`, code: "", reason: "" };
    try {
      failure = errorFromBody(await res.json());
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    throw new ApiError(res.status, failure.message, failure.code, failure.reason);
  }
  return res.text();
}

// ---- Specialized Agent Types (P0-02) ----
export interface SpecializedAgentDefinition {
  id: string;
  type: string;
  name: string;
  version: string;
  status: string;
  risk_tier: string;
  capabilities: string[];
  supported_inputs: string[];
  supported_outputs: string[];
  required_controls: string[];
}

export interface SpecializedExecutionRequest {
  environment_id: string;
  agent_version?: string;
  model_version_id?: string;
  risk_tier?: string;
  locale?: string;
  language?: string;
  payload: Record<string, unknown>;
  source_references: Array<{
    document_id?: string;
    chunk_id?: string;
    source_title?: string;
    content_fingerprint: string;
    page_number?: number;
    character_start?: number;
    character_end?: number;
    retrieval_timestamp?: string;
  }>;
  idempotency_key?: string;
}

export interface SpecializedExecutionResponse {
  id: string;
  tenant_id: string;
  organization_id: string;
  environment_id: string;
  request_id: string;
  trace_id: string;
  agent_type: string;
  agent_version: string;
  model_version_id?: string;
  risk_tier: string;
  status: string;
  input_fingerprint: string;
  output_fingerprint: string;
  policy_decision_id?: string;
  lineage_root_id?: string;
  evidence_root_hash?: string;
  review_required: boolean;
  review_state: string;
  result: Record<string, unknown>;
  failure_code?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
}

// ---- Workflow Types (P0-03) ----
export interface WorkflowDefinition {
  id: string;
  tenant_id: string;
  name: string;
  description?: string;
  status: string;
  version: number;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface WorkflowExecution {
  id: string;
  workflow_id: string;
  status: string;
  current_node?: string;
  context: Record<string, unknown>;
  result?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ---- Governance Types (P0-05) ----
export interface GovernancePolicy {
  id: string;
  tenant_id: string;
  organization_id: string;
  environment_id?: string;
  policy_type: string;
  name: string;
  description?: string;
  status: string;
  version: number;
  rules: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReviewCase {
  id: string;
  tenant_id: string;
  status: string;
  priority: string;
  subject_type: string;
  subject_id: string;
  assigned_to?: string;
  created_at: string;
  updated_at: string;
}

export interface EvidenceRecord {
  id: string;
  tenant_id: string;
  event_type: string;
  payload_hash: string;
  previous_hash?: string;
  created_at: string;
}

// ---- Voice Types (P0-04) ----
/** One tenant-owned voice profile. Mirrors `public_profile()` in
 *  `app/voice/voice_profile_service.py`. */
export interface VoiceProfile {
  id: string;
  name: string;
  provider: string;
  provider_voice_id: string;
  language: string;
  locale: string;
  voice_type: string;
  status: string;
  capabilities: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

/** Result of `POST /api/v1/testing/phone-calls/run`. */
export interface PhoneCallTestRun {
  id: string;
  status: string;
  provider: string | null;
  is_mock_provider: boolean;
}

export type TranslationExportFormat = "json" | "srt" | "txt";

/** One segment as returned by the `json` export of a job. */
export interface TranslationExportSegment {
  segment_id: string;
  source_text: string;
  translated_text: string;
  source_fingerprint: string | null;
  target_fingerprint: string | null;
  quality_flags: string[];
  review_required: boolean;
  status: string;
}

/** Parsed body of `GET /api/translation/jobs/{id}/export?format=json`. */
export interface TranslationExportDocument {
  execution_id: string;
  id: string;
  format: "json";
  status: string;
  source_language: string | null;
  target_language: string | null;
  glossary_version: string | null;
  quality_status: string | null;
  review_required: boolean;
  review_state: string | null;
  provider: string | null;
  model: string | null;
  methodology: string | null;
  segment_count: number;
  segments: TranslationExportSegment[];
  exported_at: string | null;
}

export interface TranslationJobRow {
  execution_id: string;
  id: string;
  status: string;
  execution_status: string | null;
  source_language: string;
  target_language: string;
  glossary_version: string;
  segment_count: number;
  total_segments: number;
  completed_segments: number;
  failed_segments: number;
  quality_status: string;
  review_required: boolean;
  review_state: string;
  attempt_count: number;
  created_at: string | null;
  completed_at: string | null;
  updated_at: string | null;
}

export interface TranslationJobListResponse {
  items: TranslationJobRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface VoiceProfileCreateInput {
  name: string;
  provider: string;
  provider_voice_id: string;
  language?: string;
  locale?: string;
  voice_type?: "provider" | "cloned" | "system";
  capabilities?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface VoiceCloneJob {
  id: string;
  tenant_id: string;
  status: string;
  provider: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export const api = {
  // ---- auth ----

  /**
   * Sign in.
   *
   * The server answers **202** with a second-factor challenge when the account
   * owes a factor, and 200 with tokens otherwise. The two are returned as a
   * tagged union rather than being collapsed, because a client that only
   * checks \"is this 2xx?\" would treat an unfinished login as a completed one.
   */
  async login(email: string, password: string): Promise<LoginResult> {
    const { status, body } = await perform<TokenResponse | MFAChallengeResponse>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
      { retryOn401: false },
    );
    if (status === 202) {
      return { kind: "mfa_challenge", challenge: body as MFAChallengeResponse };
    }
    return { kind: "tokens", tokens: body as TokenResponse };
  },

  me(): Promise<MeResponse> {
    return request<MeResponse>("/auth/me");
  },

  // ---- overview / analytics ----
  overview(): Promise<OverviewResponse> {
    return request<OverviewResponse>("/api/analytics/overview");
  },

  // ---- calls ----
  listCalls(
    tenantId: string,
    params: {
      limit?: number;
      offset?: number;
      status?: string;
      direction?: string;
      booked?: boolean;
      transferred?: boolean;
      search?: string;
    } = {},
  ): Promise<CallListResponse> {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") qs.set(key, String(value));
    }
    const query = qs.toString();
    return request<CallListResponse>(
      `/api/tenants/${tenantId}/calls${query ? `?${query}` : ""}`,
    );
  },

  callDetail(callId: string): Promise<CallDetail> {
    return request<CallDetail>(`/api/calls/${callId}`);
  },

  transcript(callId: string): Promise<TranscriptTurn[]> {
    return request<TranscriptTurn[]>(`/api/calls/${callId}/transcript`);
  },

  // ---- knowledge ----
  knowledgeDocuments(): Promise<DocumentListResponse> {
    return request<DocumentListResponse>("/api/knowledge/documents");
  },

  knowledgeStats(): Promise<KnowledgeStats> {
    return request<KnowledgeStats>("/api/knowledge/stats");
  },

  knowledgeSearch(query: string, topK?: number): Promise<SearchResponse> {
    return request<SearchResponse>(
      "/api/knowledge/search",
      {
        method: "POST",
        body: JSON.stringify({ query, top_k: topK ?? null }),
      },
      { retryOn401: true },
    );
  },

  // ---- integrations (CRM) ----
  crmIntegrations(): Promise<IntegrationListResponse> {
    return request<IntegrationListResponse>("/api/integrations/crm");
  },

  crmProviders(): Promise<ProviderCatalogueResponse> {
    return request<ProviderCatalogueResponse>("/api/integrations/crm/providers");
  },

  crmSyncs(): Promise<SyncListResponse> {
    return request<SyncListResponse>("/api/integrations/crm/syncs");
  },

  crmDisconnect(provider: string): Promise<void> {
    return request<void>(`/api/integrations/crm/${provider}/disconnect`, {
      method: "POST",
    });
  },

  // ---- team + audit ----
  teamUsers(): Promise<User[]> {
    return request<User[]>("/api/team/users");
  },

  teamAudit(): Promise<AuditEntry[]> {
    return request<AuditEntry[]>("/api/team/audit");
  },

  // ---- billing ----
  billingStatus(): Promise<BillingStatus> {
    return request<BillingStatus>("/api/billing");
  },

  billingPlans(): Promise<Plan[]> {
    return request<Plan[]>("/api/billing/plans");
  },

  billingUsage(): Promise<UsageResponse> {
    return request<UsageResponse>("/api/billing/usage");
  },

  billingInvoices(): Promise<Invoice[]> {
    return request<Invoice[]>("/api/billing/invoices");
  },

  // ---- appointments ----
  appointments(
    params: { status?: string; upcoming?: boolean } = {},
  ): Promise<AppointmentListResponse> {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.upcoming) qs.set("upcoming", "true");
    const query = qs.toString();
    return request<AppointmentListResponse>(
      `/api/appointments${query ? `?${query}` : ""}`,
    );
  },

  availability(day: string): Promise<AvailabilityResponse> {
    return request<AvailabilityResponse>(
      `/api/appointments/availability?day=${encodeURIComponent(day)}`,
    );
  },

  // ---- campaigns ----
  campaigns(): Promise<Campaign[]> {
    return request<Campaign[]>("/api/campaigns");
  },

  campaignResults(campaignId: string): Promise<CampaignResults> {
    return request<CampaignResults>(`/api/campaigns/${campaignId}/results`);
  },

  // ---- leads ----
  leads(tenantId: string): Promise<Lead[]> {
    return request<Lead[]>(`/api/tenants/${tenantId}/leads`);
  },

  // ---- agent ----
  agentConfig(tenantId: string): Promise<AgentConfig> {
    return request<AgentConfig>(`/api/tenants/${tenantId}/agent`);
  },

  llmPresets(): Promise<LlmPreset[]> {
    return request<LlmPreset[]>("/api/llm/presets");
  },

  // ---- specialized agents (P0-02) - Product Control Plane ----
  specializedAgents(): Promise<SpecializedAgentDefinition[]> {
    return request<SpecializedAgentDefinition[]>("/api/specialized-agents");
  },

  specializedAgentDetail(agentType: string): Promise<SpecializedAgentDefinition> {
    return request<SpecializedAgentDefinition>(`/api/specialized-agents/${agentType}`);
  },

  executeSpecializedAgent(
    agentType: string,
    payload: SpecializedExecutionRequest,
  ): Promise<SpecializedExecutionResponse> {
    return request<SpecializedExecutionResponse>(
      `/api/specialized-agents/${agentType}/execute`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  },

  getSpecializedExecution(
    agentType: string,
    executionId: string,
    environmentId: string,
  ): Promise<SpecializedExecutionResponse> {
    return request<SpecializedExecutionResponse>(
      `/api/specialized-agents/${agentType}/executions/${executionId}?environment_id=${environmentId}`,
    );
  },

  // ---- workflows (P0-03) - Visual Builder ----
  workflows(): Promise<WorkflowDefinition[]> {
    return request<WorkflowDefinition[]>("/api/workflows");
  },

  workflowDetail(workflowId: string): Promise<WorkflowDefinition> {
    return request<WorkflowDefinition>(`/api/workflows/${workflowId}`);
  },

  createWorkflow(payload: {
    name: string;
    description?: string;
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
    environment_id: string;
  }): Promise<WorkflowDefinition> {
    return request<WorkflowDefinition>("/api/workflows", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateWorkflow(
    workflowId: string,
    payload: Partial<WorkflowDefinition>,
  ): Promise<WorkflowDefinition> {
    return request<WorkflowDefinition>(`/api/workflows/${workflowId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  executeWorkflow(
    workflowId: string,
    payload: { environment_id: string; input?: Record<string, unknown> },
  ): Promise<WorkflowExecution> {
    return request<WorkflowExecution>(`/api/workflows/${workflowId}/execute`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  workflowExecutions(workflowId: string): Promise<WorkflowExecution[]> {
    return request<WorkflowExecution[]>(`/api/workflows/${workflowId}/executions`);
  },

  // ---- governance (P0-05) - Governance Center ----
  governancePolicies(params?: {
    policy_type?: string;
    status?: string;
    environment_id?: string;
  }): Promise<GovernancePolicy[]> {
    const qs = new URLSearchParams();
    if (params?.policy_type) qs.set("policy_type", params.policy_type);
    if (params?.status) qs.set("status", params.status);
    if (params?.environment_id) qs.set("environment_id", params.environment_id);
    const q = qs.toString();
    return request<GovernancePolicy[]>(`/api/governance/policies${q ? `?${q}` : ""}`);
  },

  governancePolicyDetail(policyId: string): Promise<GovernancePolicy> {
    return request<GovernancePolicy>(`/api/governance/policies/${policyId}`);
  },

  reviewCases(params?: { status?: string; priority?: string }): Promise<ReviewCase[]> {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.priority) qs.set("priority", params.priority);
    const q = qs.toString();
    return request<ReviewCase[]>(`/api/reviews${q ? `?${q}` : ""}`);
  },

  evidenceChain(params?: { event_type?: string; limit?: number }): Promise<EvidenceRecord[]> {
    const qs = new URLSearchParams();
    if (params?.event_type) qs.set("event_type", params.event_type);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return request<EvidenceRecord[]>(`/api/governance/evidence${q ? `?${q}` : ""}`);
  },

  // ---- voice (P0-04) - Voice Product Workspace ----
  /** `GET /api/agent/voice-profiles` — this tenant's stored voice profiles. */
  voiceProfiles(params?: {
    provider?: string;
    status?: string;
    voice_type?: string;
  }): Promise<{ items: VoiceProfile[]; total: number }> {
    const qs = new URLSearchParams();
    if (params?.provider) qs.set("provider", params.provider);
    if (params?.status) qs.set("status", params.status);
    if (params?.voice_type) qs.set("voice_type", params.voice_type);
    const q = qs.toString();
    return request<{ items: VoiceProfile[]; total: number }>(
      `/api/agent/voice-profiles${q ? `?${q}` : ""}`,
    );
  },

  voiceProfile(profileId: string): Promise<VoiceProfile> {
    return request<VoiceProfile>(
      `/api/agent/voice-profiles/${encodeURIComponent(profileId)}`,
    );
  },

  createVoiceProfile(payload: VoiceProfileCreateInput): Promise<VoiceProfile> {
    return request<VoiceProfile>("/api/agent/voice-profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  activateVoiceProfile(profileId: string): Promise<VoiceProfile> {
    return request<VoiceProfile>(
      `/api/agent/voice-profiles/${encodeURIComponent(profileId)}/activate`,
      { method: "POST", body: JSON.stringify({}) },
    );
  },

  deactivateVoiceProfile(profileId: string): Promise<VoiceProfile> {
    return request<VoiceProfile>(
      `/api/agent/voice-profiles/${encodeURIComponent(profileId)}/deactivate`,
      { method: "POST", body: JSON.stringify({}) },
    );
  },

  removeVoiceProfile(
    profileId: string,
  ): Promise<{ deleted: boolean; id: string }> {
    return request<{ deleted: boolean; id: string }>(
      `/api/agent/voice-profiles/${encodeURIComponent(profileId)}`,
      { method: "DELETE" },
    );
  },

  voiceCloneJobs(): Promise<VoiceCloneJob[]> {
    return request<VoiceCloneJob[]>("/api/voices/catalog");
  },

  createVoiceClone(payload: {
    name: string;
    provider: string;
    audio_url?: string;
    description?: string;
  }): Promise<VoiceCloneJob> {
    return request<VoiceCloneJob>("/api/voices/clone", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  // ---- deployment (P1-07) - Deployment Verification ----
  deploymentTargets(): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>("/api/deployment/targets");
  },

  deploymentRevisions(targetId: string): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>(
      `/api/deployment/targets/${targetId}/revisions`,
    );
  },

  // ---- compliance / industry templates ----
  industryTemplates(): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>("/api/compliance/industry-templates");
  },

  complianceFrameworks(): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>("/api/compliance/frameworks");
  },

  // ---- analytics / ROI (P1-06) ----
  roiBaselines(): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>("/api/roi/baselines");
  },

  roiResults(): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>("/api/roi/kpis");
  },

  /** Places a real version-pinned test call. `require_live_carrier` is sent as
   *  `false` so the caller sees the mock flag the backend returns rather than
   *  assuming a carrier was used. */
  startPhoneCallTest(payload: {
    agent_id: string;
    agent_version_number: number;
    to_number: string;
    from_number?: string;
    require_live_carrier?: boolean;
  }): Promise<PhoneCallTestRun> {
    return request<PhoneCallTestRun>("/api/v1/testing/phone-calls/run", {
      method: "POST",
      body: JSON.stringify({
        require_live_carrier: false,
        dynamic_variables: {},
        scripted_user_turns: [],
        evaluation_rules: [],
        ...payload,
      }),
    });
  },

  // ---- translation (P1-05) ----
  /** Jobs are environment-scoped, so an environment id is required: the list
   *  route has no tenant-wide form and guessing one would be wrong. */
  translationJobs(params: {
    environment_id: string;
    status?: string;
    source_language?: string;
    target_language?: string;
    limit?: number;
    offset?: number;
  }): Promise<TranslationJobListResponse> {
    const qs = new URLSearchParams({ environment_id: params.environment_id });
    if (params.status) qs.set("status", params.status);
    if (params.source_language) qs.set("source_language", params.source_language);
    if (params.target_language) qs.set("target_language", params.target_language);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    return request<TranslationJobListResponse>(
      `/api/translation/jobs?${qs.toString()}`,
    );
  },

  /** Same export route, parsed into the typed document for in-app review. */
  async translationExportDocument(
    executionId: string,
    environmentId: string,
  ): Promise<TranslationExportDocument> {
    const text = await this.exportTranslationJob(
      executionId,
      environmentId,
      "json",
    );
    return JSON.parse(text) as TranslationExportDocument;
  },

  translationJob(
    executionId: string,
    environmentId: string,
  ): Promise<Record<string, unknown>> {
    const qs = new URLSearchParams({ environment_id: environmentId });
    return request<Record<string, unknown>>(
      `/api/translation/jobs/${encodeURIComponent(executionId)}?${qs.toString()}`,
    );
  },

  /** Exports return a document, not JSON, so the raw text is handed back.
   *  `format=json` parses cleanly into `TranslationExportDocument`. */
  async exportTranslationJob(
    executionId: string,
    environmentId: string,
    format: TranslationExportFormat,
  ): Promise<string> {
    const qs = new URLSearchParams({
      environment_id: environmentId,
      format,
    });
    return requestText(
      `/api/translation/jobs/${encodeURIComponent(executionId)}/export?${qs.toString()}`,
    );
  },

  // ---- anomaly (P1) ----
  /** Run a detector over observations. `POST /api/anomaly/analyze` is the only
   *  analysis route the backend serves; there is no "list detections" feed. */
  anomalyAnalyze(body: {
    environment_id: string;
    model_version_id: string;
    idempotency_key: string;
    observations: Array<{ metric: string; observed_at: string; value: number }>;
    configuration: {
      detector?: "z_score" | "median_mad" | "rolling_baseline" | "sudden_change";
      threshold?: number;
      minimum_history?: number;
      rolling_window?: number;
      sudden_change_threshold?: number;
      severity_high_multiplier?: number;
      severity_critical_multiplier?: number;
    };
    risk_tier?: string;
    agent_version?: string;
    metric?: string;
  }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/anomaly/analyze", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  /** Fetch one persisted analysis. */
  anomalyExecution(
    executionId: string,
    environmentId: string,
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/anomaly/${encodeURIComponent(executionId)}?environment_id=${encodeURIComponent(environmentId)}`,
    );
  },

  // ---- connectors (P1-01) ----
  connectors(): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>("/api/connectors");
  },

  // ---- legal clause library / playbook / redline (P1-04) ----
  clauseLibrary(params?: { category?: string; q?: string }): Promise<Array<Record<string, unknown>>> {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.q) qs.set("q", params.q);
    const q = qs.toString();
    return request<Array<Record<string, unknown>>>(`/api/legal/clause-library${q ? `?${q}` : ""}`);
  },

  clauseLibraryEntry(clauseKey: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/legal/clause-library/${encodeURIComponent(clauseKey)}`);
  },

  legalPlaybooks(environmentId: string): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>(`/api/legal/playbooks?environment_id=${encodeURIComponent(environmentId)}`);
  },

  createLegalPlaybook(payload: {
    environment_id: string;
    name: string;
    clause_keys?: string[];
    status?: string;
  }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/legal/playbooks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  evaluatePlaybook(
    playbookId: string,
    payload: { environment_id: string; findings: Array<Record<string, unknown>> },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/legal/playbooks/${encodeURIComponent(playbookId)}/evaluate`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  createRedlines(payload: {
    environment_id: string;
    document_id: string;
    playbook_id: string;
    findings: Array<Record<string, unknown>>;
    evidence_references: string[];
  }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/legal/redlines", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getRedlineArtifact(artifactId: string, environmentId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/legal/redlines/${encodeURIComponent(artifactId)}?environment_id=${encodeURIComponent(environmentId)}`,
    );
  },

  // ---- QMS adapters (P1-03) ----
  qmsProviders(): Promise<{ providers: string[]; note: string }> {
    return request<{ providers: string[]; note: string }>("/api/compliance/qms/providers");
  },

  qmsHealth(payload: {
    environment_id: string;
    provider: string;
    config?: Record<string, unknown>;
    credentials?: Record<string, unknown>;
  }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/compliance/qms/health", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  qmsDocuments(provider: string, environmentId: string, q?: string): Promise<Record<string, unknown>> {
    const qs = new URLSearchParams({ environment_id: environmentId });
    if (q) qs.set("q", q);
    return request<Record<string, unknown>>(
      `/api/compliance/qms/${encodeURIComponent(provider)}/documents?${qs.toString()}`,
    );
  },

  qmsTraceability(
    provider: string,
    environmentId: string,
    subjectType: string,
    subjectId: string,
  ): Promise<Record<string, unknown>> {
    const qs = new URLSearchParams({
      environment_id: environmentId,
      subject_type: subjectType,
      subject_id: subjectId,
    });
    return request<Record<string, unknown>>(
      `/api/compliance/qms/${encodeURIComponent(provider)}/traceability?${qs.toString()}`,
    );
  },

  qmsAuditPackage(
    provider: string,
    payload: { environment_id: string; framework_id: string; config?: Record<string, unknown>; credentials?: Record<string, unknown> },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/compliance/qms/${encodeURIComponent(provider)}/audit-package`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  // ---- Multi-Agent Builder, Versions, Flow, Tools & Knowledge (Part 5 / Gate G6) ----
  listAgents(params?: { status?: string; search?: string }): Promise<Array<Record<string, unknown>>> {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.search) qs.set("search", params.search);
    const q = qs.toString();
    return request<Array<Record<string, unknown>>>(`/api/agents${q ? `?${q}` : ""}`);
  },

  createAgent(payload: {
    name: string;
    description?: string;
    template_key?: string;
    initial_config?: Record<string, unknown>;
  }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getAgent(agentId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}`);
  },

  patchAgent(
    agentId: string,
    payload: Record<string, unknown>,
    expectedEtag?: string,
  ): Promise<Record<string, unknown>> {
    const headers: Record<string, string> = {};
    if (expectedEtag) headers["If-Match"] = expectedEtag;
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
  },

  duplicateAgent(
    agentId: string,
    payload?: { name?: string; include_draft?: boolean },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/clone`,
      {
        method: "POST",
        body: JSON.stringify({
          new_name: payload?.name ?? "Agent (Copy)",
          include_knowledge_bases: true,
          include_tools: true,
        }),
      },
    );
  },

  archiveAgent(agentId: string, reason = "Archived via dashboard"): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/archive`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    );
  },

  getAgentDraft(agentId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}`);
  },

  putAgentDraft(
    agentId: string,
    config: Record<string, unknown>,
    expectedEtag?: string,
  ): Promise<Record<string, unknown>> {
    const headers: Record<string, string> = {};
    if (expectedEtag) headers["If-Match"] = expectedEtag;
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ config, expected_etag: expectedEtag ?? null }),
    });
  },

  validateAgentDraft(
    agentId: string,
    config?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}/validate`, {
      method: "POST",
      body: JSON.stringify(config ? { config } : {}),
    });
  },

  publishAgentVersion(
    agentId: string,
    payload: { changelog?: string; environment?: "development" | "staging" | "production" },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}/publish`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listAgentVersions(agentId: string): Promise<Array<Record<string, unknown>>> {
    return request<Array<Record<string, unknown>>>(
      `/api/agents/${encodeURIComponent(agentId)}/versions`,
    );
  },

  diffAgentVersions(
    agentId: string,
    fromVersion: number,
    toVersion: number,
  ): Promise<Record<string, unknown>> {
    const qs = new URLSearchParams({
      from_version: String(fromVersion),
      to_version: String(toVersion),
    });
    return request<Record<string, unknown>>(
      `/api/agents/${encodeURIComponent(agentId)}/versions/diff?${qs.toString()}`,
    );
  },

  rollbackAgentVersion(
    agentId: string,
    payload: { target_version: number; reason?: string },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}/rollback`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getAgentFlow(agentId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}/flow`);
  },

  putAgentFlow(
    agentId: string,
    payload: {
      mode?: "single_prompt" | "flow";
      flow: Record<string, unknown>;
      expected_etag?: string | null;
    },
  ): Promise<Record<string, unknown>> {
    const headers: Record<string, string> = {};
    if (payload.expected_etag) headers["If-Match"] = payload.expected_etag;
    return request<Record<string, unknown>>(`/api/agents/${encodeURIComponent(agentId)}/flow`, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        mode: payload.mode ?? "flow",
        flow: payload.flow,
        expected_etag: payload.expected_etag ?? null,
      }),
    });
  },

  validateAgentFlow(
    agentId: string,
    flow?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/agents/${encodeURIComponent(agentId)}/flow/validate`,
      {
        method: "POST",
        body: JSON.stringify(flow ? { flow } : {}),
      },
    );
  },

  simulateAgentFlow(
    agentId: string,
    payload: {
      turns: string[];
      initial_variables?: Record<string, unknown>;
      flow?: Record<string, unknown>;
      judge_decisions?: Record<string, boolean>;
      tool_results?: Record<string, Record<string, unknown>>;
    },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/agents/${encodeURIComponent(agentId)}/flow/simulate`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },

  publishAgentFlow(
    agentId: string,
    payload?: { changelog?: string; environment?: "development" | "staging" | "production" },
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/api/agents/${encodeURIComponent(agentId)}/flow/publish`,
      {
        method: "POST",
        body: JSON.stringify(payload ?? {}),
      },
    );
  },

  listAgentTools(agentId?: string): Promise<Record<string, unknown>> {
    const qs = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request<Record<string, unknown>>(`/api/api-tools${qs}`);
  },

  createAgentTool(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/api-tools", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listKnowledgeCollections(): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/kb/collections");
  },

  createKnowledgeCollection(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/kb/collections", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listWorkflowTriggers(agentId?: string): Promise<Record<string, unknown>> {
    const qs = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request<Record<string, unknown>>(`/api/workflows/triggers${qs}`);
  },

  createWorkflowTrigger(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/api/workflows/triggers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
};
