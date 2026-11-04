/**
 * dashboard/src/api/campaigns.ts
 * Campaign CRUD, lifecycle control, audience/eligibility inspection, contact
 * import and dialer-policy status.
 *
 * Route sources (verified, not guessed):
 *   app/api/campaign_routes.py        → prefix `/api/campaigns`
 *   app/api/dialer_v2_routes.py       → prefix `/api/cc2/dialer`
 *   app/api/lead_import_routes.py     → `POST /api/tenants/{tenant_id}/lead-imports`
 *                                       (raw CSV body, `content-type: text/csv`)
 *
 * `POST /api/campaigns/{id}/plan` returns the ordered execution intents, and
 * `GET /api/campaigns/{id}/kpis` returns the fixed five-field progress document
 * that campaign_service.progress() builds.
 */
import { client } from './client';

export interface Campaign {
  id: string;
  tenant_id: string;
  environment_id: string;
  name: string;
  goal: string;
  channel: string;
  state: string;
  script_prompt: string;
  opening_line: string;
  lead_ids: string[];
  segment_ids: string[];
  daily_start: string;
  daily_end: string;
  days_of_week: number[];
  timezone: string;
  calls_per_minute: number;
  daily_limit: number;
  max_attempts_per_lead: number;
}

/** The writable subset: `CampaignCreateRequest` / `CampaignUpdateRequest`. */
export interface CampaignInput {
  name: string;
  goal?: string;
  channel?: string;
  script_prompt?: string;
  opening_line?: string;
  lead_ids?: string[];
  segment_ids?: string[];
  daily_start?: string;
  daily_end?: string;
  days_of_week?: number[];
  timezone?: string;
  calls_per_minute?: number;
  daily_limit?: number;
  max_attempts_per_lead?: number;
  environment_id?: string | null;
}

export interface CampaignKpis {
  total_leads: number;
  attempted: number;
  conversions: number;
  dnc_skipped: number;
  failed: number;
}

export interface CampaignResults {
  metrics: Record<string, unknown>;
  eligibility: Record<string, unknown>;
}

export interface CampaignAudience {
  segment_ids: string[];
  lead_count: number;
}

export interface CampaignEligibility {
  eligible: number;
  skipped: Record<string, number>;
  intents: Array<Record<string, unknown>>;
}

export interface ExecutionIntent {
  lead_id?: string;
  phone?: string;
  scheduled_at?: string;
  attempt?: number;
  [key: string]: unknown;
}

/** `row.as_dict()` of a CC2 dialer policy (app/contact_center_v2/dialer.py). */
export interface DialerPolicy {
  id: string;
  tenant_id: string;
  environment_id: string | null;
  name?: string;
  mode: string;
  status?: string;
  max_lines?: number;
  [key: string]: unknown;
}

export interface DialerPacingSnapshot {
  id: string;
  mode: string;
  answer_rate: string;
  occupancy: string;
  dial_ratio: string;
  projected_abandon_rate: string;
  [key: string]: unknown;
}

export interface DialerPacing {
  snapshots: DialerPacingSnapshot[];
}

export interface LeadImportResult {
  [key: string]: unknown;
}

function envQuery(environmentId?: string | null): string {
  return environmentId ? `?environment_id=${encodeURIComponent(environmentId)}` : '';
}

/* ------------------------------------------------------------------ campaigns */

export function listCampaigns(environmentId?: string | null): Promise<Campaign[]> {
  return client.get<Campaign[]>(`/api/campaigns` + envQuery(environmentId));
}

export function getCampaign(campaignId: string, environmentId?: string | null): Promise<Campaign> {
  return client.get<Campaign>(`/api/campaigns/${encodeURIComponent(campaignId)}` + envQuery(environmentId));
}

export function createCampaign(input: CampaignInput): Promise<Campaign> {
  return client.post<Campaign>('/api/campaigns', input);
}

export function updateCampaign(campaignId: string, input: CampaignInput): Promise<Campaign> {
  return client.patch<Campaign>(`/api/campaigns/${encodeURIComponent(campaignId)}`, input);
}

export function getCampaignAudience(campaignId: string, environmentId?: string | null): Promise<CampaignAudience> {
  return client.get<CampaignAudience>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/audience` + envQuery(environmentId),
  );
}

export function getCampaignEligibility(campaignId: string, environmentId?: string | null): Promise<CampaignEligibility> {
  return client.get<CampaignEligibility>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/eligibility` + envQuery(environmentId),
  );
}

export function getCampaignKpis(campaignId: string, environmentId?: string | null): Promise<CampaignKpis> {
  return client.get<CampaignKpis>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/kpis` + envQuery(environmentId),
  );
}

export function getCampaignResults(campaignId: string, environmentId?: string | null): Promise<CampaignResults> {
  return client.get<CampaignResults>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/results` + envQuery(environmentId),
  );
}

export function getCampaignExecutionStatus(campaignId: string, environmentId?: string | null): Promise<Campaign> {
  return client.get<Campaign>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/execution-status` + envQuery(environmentId),
  );
}

/** Ordered execution intents the scheduler would enqueue, bounded by `limit`. */
export function planCampaign(campaignId: string, limit = 50, environmentId?: string | null): Promise<ExecutionIntent[]> {
  const separator = environmentId ? '&' : '?';
  return client.post<ExecutionIntent[]>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/plan?limit=${encodeURIComponent(String(limit))}${
      environmentId ? `${separator}environment_id=${encodeURIComponent(environmentId)}` : ''
    }`,
  );
}

export function scheduleCampaign(campaignId: string, environmentId?: string | null): Promise<Campaign> {
  return client.post<Campaign>(`/api/campaigns/${encodeURIComponent(campaignId)}/schedule` + envQuery(environmentId));
}

export function pauseCampaign(campaignId: string, environmentId?: string | null): Promise<Campaign> {
  return client.post<Campaign>(`/api/campaigns/${encodeURIComponent(campaignId)}/pause` + envQuery(environmentId));
}

export function resumeCampaign(campaignId: string, environmentId?: string | null): Promise<Campaign> {
  return client.post<Campaign>(`/api/campaigns/${encodeURIComponent(campaignId)}/resume` + envQuery(environmentId));
}

export function cancelCampaign(campaignId: string, environmentId?: string | null): Promise<Campaign> {
  return client.post<Campaign>(`/api/campaigns/${encodeURIComponent(campaignId)}/cancel` + envQuery(environmentId));
}

/* ------------------------------------------------------------- contact import */

/**
 * Import contacts from a CSV payload.
 *
 * The route reads the raw request body (bounded by `app.leads.importers.MAX_BYTES`)
 * and dispatches on `content-type`, so the CSV is posted verbatim rather than
 * wrapped in JSON.
 */
export function importContactsCsv(
  tenantId: string,
  csv: string,
  options: { environmentId?: string | null; idempotencyKey?: string } = {},
): Promise<LeadImportResult> {
  const params = new URLSearchParams();
  if (options.environmentId) params.set('environment_id', options.environmentId);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return client.post<LeadImportResult>(
    `/api/tenants/${encodeURIComponent(tenantId)}/lead-imports${suffix}`,
    csv,
    {
      raw: true,
      headers: {
        'Content-Type': 'text/csv',
        ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}),
      },
    },
  );
}

/* -------------------------------------------------------------- dialer status */

export function createDialerPolicy(policy: Record<string, unknown>): Promise<{ policy: DialerPolicy }> {
  return client.post<{ policy: DialerPolicy }>('/api/cc2/dialer/policies', policy);
}

export function getDialerPolicy(policyId: string): Promise<{ policy: DialerPolicy }> {
  return client.get<{ policy: DialerPolicy }>(`/api/cc2/dialer/policies/${encodeURIComponent(policyId)}`);
}

export function getDialerPacing(policyId: string, limit = 50): Promise<DialerPacing> {
  return client.get<DialerPacing>(
    `/api/cc2/dialer/policies/${encodeURIComponent(policyId)}/pacing?limit=${encodeURIComponent(String(limit))}`,
  );
}

export function startDialerPolicy(policyId: string): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>(`/api/cc2/dialer/policies/${encodeURIComponent(policyId)}/start`);
}

export function pauseDialerPolicy(policyId: string): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>(`/api/cc2/dialer/policies/${encodeURIComponent(policyId)}/pause`);
}

export function stopDialerPolicy(policyId: string): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>(`/api/cc2/dialer/policies/${encodeURIComponent(policyId)}/stop`);
}
