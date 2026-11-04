/**
 * dashboard/src/api/integrations.ts
 * Authenticated integration management (CRM adapters + Salesforce).
 *
 * Route source: app/api/integration_routes.py — `APIRouter(prefix="/api/integrations")`,
 * plus app/api/salesforce_routes.py. The instructions for this batch referred to
 * an "integrations v2" API; there is no `/api/integrations/v2` router mounted in
 * app/main.py (checked against the live route table), so this client targets the
 * routers that actually exist. Nothing is invented here.
 *
 * Boundaries encoded in the types:
 *   • `IntegrationIn.credentials` is write-only — no response model echoes it,
 *     so `Integration` has no credentials field at all.
 *   • `HealthOut` carries a scrubbed `safe_message`, never a raw provider error.
 */
import { client } from './client';

export type CrmProvider = 'gohighlevel' | 'hubspot' | 'salesforce' | 'pipedrive' | 'zoho';

export interface Integration {
  provider: string;
  is_enabled: boolean;
  connected: boolean;
  capabilities?: string[];
  config?: Record<string, unknown>;
  field_mappings?: Record<string, unknown>;
  subscribed_events?: string[];
  share_transcripts?: boolean;
  connected_at?: string | null;
  last_health_check_at?: string | null;
  last_health_ok?: boolean | null;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface IntegrationInput {
  is_enabled?: boolean;
  credentials?: Record<string, string> | null;
  config?: Record<string, unknown>;
  field_mappings?: Record<string, unknown>;
  subscribed_events?: string[];
  share_transcripts?: boolean;
}

export interface ProviderInfo {
  provider: string;
  capabilities: string[];
  credential_fields: string[];
  config_fields: string[];
}

export interface IntegrationHealth {
  connected: boolean;
  provider: string;
  latency_ms: number;
  safe_message: string;
}

export interface SyncRun {
  id: string;
  provider: string;
  entity_type: string;
  entity_id: string;
  event_type?: string | null;
  status: string;
  external_id?: string | null;
  attempt_count: number;
  last_attempt_at?: string | null;
  next_attempt_at?: string | null;
  synced_at?: string | null;
  last_error?: string | null;
  last_error_code?: string | null;
}

export interface SyncList {
  syncs: SyncRun[];
  total: number;
}

export function listIntegrations(): Promise<{ integrations: Integration[] }> {
  return client.get<{ integrations: Integration[] }>('/api/integrations/crm');
}

export function listProviders(): Promise<{ providers: ProviderInfo[] }> {
  return client.get<{ providers: ProviderInfo[] }>('/api/integrations/crm/providers');
}

export function getIntegration(provider: string): Promise<Integration> {
  return client.get<Integration>(`/api/integrations/crm/${encodeURIComponent(provider)}`);
}

/** Connect or update. Omitting `credentials` leaves the stored secret untouched. */
export function connectIntegration(provider: string, input: IntegrationInput): Promise<Integration> {
  return client.put<Integration>(`/api/integrations/crm/${encodeURIComponent(provider)}`, input);
}

export function disconnectIntegration(provider: string): Promise<Integration> {
  return client.post<Integration>(`/api/integrations/crm/${encodeURIComponent(provider)}/disconnect`);
}

export function testIntegration(provider: string): Promise<IntegrationHealth> {
  return client.post<IntegrationHealth>(`/api/integrations/crm/${encodeURIComponent(provider)}/test`);
}

export function deleteIntegration(provider: string): Promise<null> {
  return client.del<null>(`/api/integrations/crm/${encodeURIComponent(provider)}`);
}

export function listSyncRuns(params: { status?: string; provider?: string; limit?: number } = {}): Promise<SyncList> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (params.provider) qs.set('provider', params.provider);
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return client.get<SyncList>(`/api/integrations/crm/syncs${suffix}`);
}

export function setFieldMappings(provider: string, fieldMappings: Record<string, unknown>): Promise<Integration> {
  return client.put<Integration>(`/api/integrations/crm/${encodeURIComponent(provider)}`, {
    field_mappings: fieldMappings,
  });
}

/* ------------------------------------------------------------- Salesforce ---
 * The Salesforce adapter has its own router with a richer connection model; the
 * console lists it alongside the CRM providers but flips the same three levers
 * (connect / health / sync) through these routes.
 */

export function salesforceConnections(): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>('/api/integrations/salesforce/connections');
}

export function salesforceHealth(): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>('/api/integrations/salesforce/health');
}

export function salesforceStats(): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>('/api/integrations/salesforce/stats');
}

export function salesforceSync(entityType?: string): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>('/api/integrations/salesforce/sync', entityType ? { entity_type: entityType } : {});
}

export function salesforceDisconnect(): Promise<Record<string, unknown>> {
  return client.del<Record<string, unknown>>('/api/integrations/salesforce/connection');
}
