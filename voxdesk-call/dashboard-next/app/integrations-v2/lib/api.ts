/**
 * Typed fetch client for Integrations v2 routes (/api/v1/integrations, /api/v1/collab, /api/v1/agent-tools).
 */

import { BASE_URL } from "@/lib/api";
import { getToken } from "@/lib/auth";

export type UiConnectionStatus = "CONFIGURED" | "NOT_CONFIGURED" | "ERROR";
export type VerificationTier = "LIVE_VERIFIED" | "PROTOCOL_TESTED" | "NOT_CONFIGURED";

export interface CatalogProvider {
  slug: string;
  display_name: string;
  category: "helpdesk" | "crm" | "collab";
  auth_modes: string[];
  capabilities: string[];
  default_scopes: string[];
  webhook_signature_header: string;
  docs_note: string;
  env_configured: boolean;
  ui_status: UiConnectionStatus;
  connection_id: string | null;
  last_health_ok: boolean | null;
  last_health_at: string | null;
  last_error: string;
  verification_status: VerificationTier;
}

export interface ConnectionMasked {
  id: string;
  tenant_id: string;
  provider: string;
  category: "helpdesk" | "crm" | "collab";
  display_name: string;
  auth_kind: "oauth2" | "api_key";
  status: string;
  external_account_id: string;
  instance_url: string;
  region: string;
  scopes: string[];
  token_expires_at: string | null;
  has_refresh_token: boolean;
  has_webhook_secret: boolean;
  last_health_at: string | null;
  last_health_ok: boolean | null;
  last_error: string;
  config: Record<string, unknown>;
}

export interface FieldMappingRuleRow {
  id?: string;
  entity_type: string;
  direction: "inbound" | "outbound" | "bidirectional";
  voxdesk_field: string;
  external_field: string;
  transform: string;
  transform_config: Record<string, unknown>;
  required: boolean;
  default_value: string | null;
}

export interface SyncRunRow {
  id: string;
  connection_id: string;
  entity_type: string;
  status: string;
  cursor_before: string;
  cursor_after: string;
  records_seen: number;
  records_upserted: number;
  records_failed: number;
  dead_letters: number;
  error_summary: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface MatrixProviderRow {
  provider: string;
  display_name: string;
  category: string;
  verification_status: VerificationTier;
  env_configured: boolean;
  tenant_connected: boolean;
  last_verified_at: string | null;
  capabilities: Record<string, VerificationTier>;
  honesty_notes: string;
}

export interface NotificationRuleItem {
  id: string;
  connection_id: string;
  event_type: string;
  team_id: string;
  channel_id: string;
  channel_name: string;
  template: string;
  rate_limit_per_min: number;
  dedupe_window_seconds: number;
  enabled: boolean;
  filter_config: Record<string, unknown>;
}

export interface AgentToolSpec {
  name: string;
  description: string;
  category: "helpdesk" | "crm" | "collab";
  parameters_schema: Record<string, unknown>;
  mutating: boolean;
}

export interface ToolEnablementItem {
  id: string;
  agent_id: string;
  tool_name: string;
  connection_id: string;
  enabled: boolean;
  max_calls_per_conversation: number;
  timeout_ms: number;
}

async function int2Request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers,
  });
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`;
    try {
      const errBody = await resp.json();
      if (typeof errBody?.detail === "string") {
        message = errBody.detail;
      } else if (errBody?.detail?.message) {
        message = String(errBody.detail.message);
      }
    } catch {
      // keep status fallback
    }
    throw new Error(message);
  }
  return (await resp.json()) as T;
}

export const int2Api = {
  getCatalog: () =>
    int2Request<{
      providers: CatalogProvider[];
      supported_transforms: string[];
      supported_entities: string[];
      supported_directions: string[];
    }>("/api/v1/integrations/catalog"),

  getMatrix: () =>
    int2Request<{
      summary: Record<string, number>;
      providers: MatrixProviderRow[];
    }>("/api/v1/integrations/matrix"),

  listConnections: (category?: string) =>
    int2Request<{ connections: ConnectionMasked[] }>(
      `/api/v1/integrations/connections${category ? `?category=${encodeURIComponent(category)}` : ""}`
    ),

  connectProvider: (
    provider: string,
    payload: {
      instance_url?: string;
      region?: string;
      redirect_uri?: string;
      scopes?: string[];
      extra_params?: Record<string, unknown>;
      credentials?: Record<string, string>;
      webhook_secret?: string;
      display_name?: string;
      external_account_id?: string;
      config?: Record<string, unknown>;
      verify_now?: boolean;
    }
  ) =>
    int2Request<{
      authorize_url?: string;
      expires_at?: string;
      connection?: ConnectionMasked;
    }>(`/api/v1/integrations/${encodeURIComponent(provider)}/connect`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  completeOAuthCallback: (payload: {
    provider: string;
    code: string;
    state: string;
    display_name?: string;
    external_account_id?: string;
  }) =>
    int2Request<{ connection: ConnectionMasked }>(
      "/api/v1/integrations/v2/oauth/callback",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),

  testConnection: (connectionId: string) =>
    int2Request<{
      health: { ok: boolean; latency_ms: number; detail: string; account_id: string };
      connection: ConnectionMasked;
    }>(`/api/v1/integrations/connections/${connectionId}/test`, { method: "POST" }),

  refreshConnection: (connectionId: string) =>
    int2Request<{ connection: ConnectionMasked }>(
      `/api/v1/integrations/connections/${connectionId}/refresh`,
      { method: "POST" }
    ),

  disconnectConnection: (connectionId: string) =>
    int2Request<{ deleted: boolean; provider: string; token_revoked: boolean }>(
      `/api/v1/integrations/connections/${connectionId}`,
      { method: "DELETE" }
    ),

  listMappings: (connectionId: string, entityType = "Contact") =>
    int2Request<{ rules: FieldMappingRuleRow[] }>(
      `/api/v1/integrations/connections/${connectionId}/mapping?entity_type=${encodeURIComponent(entityType)}`
    ),

  saveMappings: (
    connectionId: string,
    entityType: string,
    rules: FieldMappingRuleRow[]
  ) =>
    int2Request<{ entity_type: string; saved_count: number }>(
      `/api/v1/integrations/connections/${connectionId}/mapping`,
      {
        method: "PUT",
        body: JSON.stringify({ entity_type: entityType, rules }),
      }
    ),

  fetchLiveSamplePayload: (connectionId: string, entityType: "Contact" | "Ticket") =>
    int2Request<{ entity_type: string; sample: Record<string, unknown> | null }>(
      `/api/v1/integrations/connections/${connectionId}/sample-payload?entity_type=${encodeURIComponent(entityType)}`
    ),

  previewMappings: (payload: {
    direction: "inbound" | "outbound";
    rules: FieldMappingRuleRow[];
    sample_payload: Record<string, unknown>;
  }) =>
    int2Request<{
      ok: boolean;
      output: Record<string, unknown>;
      errors: Array<{ field: string; message: string }>;
    }>("/api/v1/integrations/connections/mapping/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  triggerSync: (
    connectionId: string,
    entityType: "Contact" | "Lead" | "Ticket",
    conflictPolicy: "external_wins" | "voxdesk_wins" | "newest_wins" = "newest_wins"
  ) =>
    int2Request<{ sync_run: SyncRunRow; replayed: boolean }>(
      `/api/v1/integrations/connections/${connectionId}/sync`,
      {
        method: "POST",
        body: JSON.stringify({
          entity_type: entityType,
          conflict_policy: conflictPolicy,
        }),
      }
    ),

  listSyncRuns: (connectionId?: string) =>
    int2Request<{ sync_runs: SyncRunRow[] }>(
      connectionId
        ? `/api/v1/integrations/connections/${connectionId}/sync-runs`
        : "/api/v1/integrations/sync-runs"
    ),

  listNotificationRules: (connectionId?: string) =>
    int2Request<{ supported_events: string[]; rules: NotificationRuleItem[] }>(
      `/api/v1/collab/rules${connectionId ? `?connection_id=${connectionId}` : ""}`
    ),

  saveNotificationRule: (payload: {
    connection_id: string;
    event_type: string;
    team_id?: string;
    channel_id: string;
    channel_name?: string;
    template?: string;
    rate_limit_per_min?: number;
    dedupe_window_seconds?: number;
    enabled?: boolean;
  }) =>
    int2Request<{ rule: NotificationRuleItem }>("/api/v1/collab/rules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteNotificationRule: (ruleId: string) =>
    int2Request<{ deleted: boolean }>(`/api/v1/collab/rules/${ruleId}`, {
      method: "DELETE",
    }),

  sendTestNotification: (payload: {
    rule_id?: string;
    connection_id?: string;
    team_id?: string;
    channel_id?: string;
    title?: string;
    summary: string;
  }) =>
    int2Request<{
      provider: string;
      status: string;
      message_id: string;
      rendered_text?: string;
    }>("/api/v1/collab/notify/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getAgentToolEnablements: (agentId = "") =>
    int2Request<{
      specs: AgentToolSpec[];
      enablements: ToolEnablementItem[];
      active_mcp_tools: Array<{ name: string; description: string }>;
    }>(
      `/api/v1/agent-tools/enablements${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`
    ),

  upsertToolEnablement: (payload: {
    agent_id?: string;
    tool_name: string;
    connection_id: string;
    enabled: boolean;
    max_calls_per_conversation?: number;
    timeout_ms?: number;
  }) =>
    int2Request<{ enablement: ToolEnablementItem }>(
      "/api/v1/agent-tools/enablements",
      {
        method: "PUT",
        body: JSON.stringify(payload),
      }
    ),

  dryRunTool: (toolName: string, payload: { arguments: Record<string, unknown>; agent_id?: string }) =>
    int2Request<{
      valid: boolean;
      tool_name: string;
      vendor_called: boolean;
      errors: string[];
      connection_id?: string | null;
    }>(`/api/v1/agent-tools/tools/${encodeURIComponent(toolName)}/dry-run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
