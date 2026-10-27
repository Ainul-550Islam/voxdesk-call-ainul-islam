/**
 * Typed client for the Batch 07 platform surface.
 *
 * Three API areas share one client because an operator moving between them is doing
 * one job — "is the platform healthy for this tenant" — and switching clients would
 * only add three ways to get the base URL wrong:
 *
 * * `/api/wfm`          workforce management (forecast, staffing, schedule, time off)
 * * `/api/whitelabel`   custom domains, theme, assets, sender domains, widget
 * * `/api/mobile`       devices, push deliveries, provider status
 *
 * Two conventions are enforced here rather than at each call site:
 *
 * * **Writes carry an idempotency key.** The backend accepts `Idempotency-Key` and
 *   replays the stored response instead of doing the work twice; a UI that retries a
 *   publish after a timeout must therefore send the *same* key. `mutate()` takes the
 *   key from the caller so a retry loop can hold it.
 * * **A 204 is not JSON.** Several delete routes return an empty body; parsing it
 *   would throw `Unexpected end of JSON input`.
 */

import { BASE_URL } from "@/lib/api";
import { getToken } from "@/lib/auth";

export const WFM_BASE = `${BASE_URL}/api/wfm`;
export const WHITELABEL_BASE = `${BASE_URL}/api/whitelabel`;
export const MOBILE_BASE = `${BASE_URL}/api/mobile`;
export const TLS_BASE = `${BASE_URL}/api/platform/tls`;

export class Plat2ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "Plat2ApiError";
    this.status = status;
    this.code = code;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function errorFrom(status: number, payload: unknown): Plat2ApiError {
  // FastAPI's `detail` is either a string, the Batch 07 `{code, message}` envelope,
  // or a 422 validation list. Read all three; never invent a message.
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return new Plat2ApiError(detail, status, `http_${status}`);
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const envelope = detail as { code?: unknown; message?: unknown };
    const message =
      typeof envelope.message === "string" && envelope.message
        ? envelope.message
        : `Request failed with status ${status}`;
    const code = typeof envelope.code === "string" && envelope.code ? envelope.code : `http_${status}`;
    return new Plat2ApiError(message, status, code);
  }
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: unknown[]; msg?: unknown };
    const where = Array.isArray(first.loc) ? first.loc.slice(1).join(".") : "request";
    const message = typeof first.msg === "string" ? first.msg : "Validation failed";
    return new Plat2ApiError(`${where}: ${message}`, status, "validation_failed");
  }
  return new Plat2ApiError(`Request failed with status ${status}`, status, `http_${status}`);
}

async function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    credentials: "include",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    ...(options.signal ? { signal: options.signal } : {}),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) throw errorFrom(response.status, payload);
  return payload as T;
}

export function idempotencyKey(prefix: string): string {
  // `crypto.randomUUID` exists in every browser this console supports and in Node 20
  // (the vitest environment); the timestamp keeps keys sortable when reading logs.
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2).padEnd(32, "0");
  return `${prefix}:${Date.now().toString(36)}:${random}`;
}

// --------------------------------------------------------------------- types ---

export interface ForecastPointSummary {
  /** ISO date (tenant-local) of the day these totals belong to. */
  date: string;
  expected: number;
}

export interface ForecastSummary {
  id: string;
  queue_key: string;
  timezone: string;
  interval_minutes: number;
  generated_at: string;
  horizon_days: number;
  history_days: number;
  method: string;
  model_version: string;
  points: number;
  total_expected: number | null;
  peak: number | null;
  overrides: Record<string, unknown>;
  daily_totals: Record<string, number>;
}

export interface ForecastCurvePoint {
  interval_start: string;
  interval_minutes: number;
  expected: number;
  low: number;
  high: number;
}

/**
 * The detail endpoint repeats the list summary and adds the per-interval curve. There
 * is no separate `result` envelope: the forecast *is* the row, and the curve is the
 * only extra.
 */
export interface ForecastDetail extends ForecastSummary {
  curve: ForecastCurvePoint[];
}

/** One published record a tenant must create, as the create call described it. */
export interface SenderRecordSpec {
  record_type: string;
  name: string;
  value: string;
  purpose: string;
}

export type SenderRecordsMap = {
  spf?: SenderRecordSpec;
  dkim?: SenderRecordSpec;
  dmarc?: SenderRecordSpec;
};

export interface StaffingTarget {
  service_level: number;
  answer_seconds: number;
  max_occupancy: number;
  shrinkage: number;
  average_handle_seconds: number;
  average_patience_seconds: number;
  minimum_agents: number;
  paid_multiplier: number;
  /** `erlang_c` or `erlang_a`; the plan names the model it used. */
  model: string;
}

export interface StaffingInterval {
  interval_start: string;
  interval_minutes: number;
  volume: number;
  productive_agents: number;
  paid_agents: number;
  service_level: number;
  occupancy: number;
  offered_load: number;
}

export interface StaffingPlan {
  queue_key: string;
  timezone: string;
  /** Which forecast band the curve was built from (`low` | `expected` | `high`). */
  band: string;
  target: StaffingTarget;
  intervals: number;
  peak_paid_agents: number;
  peak_at: string | null;
  productive_hours: number;
  paid_hours: number;
  daily: Record<string, { paid_agents_peak: number; paid_hours: number; volume: number }>;
  curve: StaffingInterval[];
  distribution: { min: number; median: number; mean: number; max: number; p90: number };
}

export interface AccuracyEvaluation {
  forecast_id: string;
  evaluated_from: string;
  evaluated_to: string;
  /** Fractions, not percentages: 0.128 is a 12.8% error. */
  wape: number;
  mape: number;
  bias: number;
  mae: number;
  sample_count: number;
}

export interface AccuracyTrend {
  /** How many evaluations the trend is built from. */
  samples: number;
  mean_wape: number;
  last_wape: number;
  /** `improving` | `worsening` | `flat` | `unknown`, computed by the backend. */
  direction: string;
}

export interface AccuracyResponse {
  queue_key: string;
  count: number;
  trend: AccuracyTrend;
  evaluations: AccuracyEvaluation[];
}

export interface HistoryCounts {
  /** Stored interval rows for the queue. */
  intervals: number;
  /** Distinct days covered by those rows. */
  days: number;
  first: string | null;
  last: string | null;
  /** Days the forecast model insists on before it will produce a multiplicative fit. */
  required_days: number;
  /** False means a forecast would be refused, not that it would be thin. */
  enough: boolean;
  workday_minutes: number;
}

export interface ShiftTemplateRow {
  id: string;
  name: string;
  start_minute: number;
  length_minutes: number;
  paid_break_minutes: number;
  unpaid_break_minutes: number;
  required_skill_key: string;
  is_active: boolean;
}

export interface LaborRuleRow {
  id: string;
  name: string;
  max_hours_per_week: number;
  min_rest_hours: number;
  max_consecutive_days: number;
  max_shift_hours: number;
  min_split_gap_minutes: number;
  is_active: boolean;
}

export interface ScheduleRow {
  id: string;
  name: string;
  week_start: string;
  timezone: string;
  status: string;
  current_version_id: string | null;
}



export interface ScheduleShiftRow {
  shift_id: string;
  user_id: string;
  template_id: string;
  starts_at_utc: string;
  ends_at_utc: string;
  starts_at_local: string;
  ends_at_local: string;
  break_minutes: number;
  paid_hours: number;
  skill_key: string;
  status: string;
}

export interface ScheduleDetail extends ScheduleRow {
  versions: ScheduleVersionSummary[];
  shifts: ScheduleShiftRow[];
}

export interface PublishResult {
  schedule_id: string;
  version_id: string;
  version_number: number;
  published_at: string;
  diff: Record<string, unknown>;
  notifications: Array<{ user_id: string; changes: number; items: Array<Record<string, unknown>> }>;
}

export interface ScheduleVersionSummary {
  version_id: string;
  version_number: number;
  status: string;
  /** How many shifts the version holds — not the rows themselves. */
  assignments: number;
  coverage: Record<string, unknown>;
  solver: {
    status?: string | null;
    objective?: number | null;
    wall_time_seconds?: number | null;
    coverage_ratio?: number | null;
  };
  created_at: string | null;
  published_at: string | null;
}

export interface SolveReport {
  status: string;
  assignments: number;
  hours_by_user: Record<string, number>;
  /** Agent-intervals where the rota leaves the queue short, and where it over-staffs. */
  shortfall_agent_intervals: number;
  overcoverage_agent_intervals: number;
  objective: number;
  wall_time_seconds: number;
  intervals: number;
  coverage_ratio: number;
  rules: Record<string, unknown>;
  version?: ScheduleVersionSummary;
}

export interface TimeOffRow {
  id: string;
  user_id: string;
  kind: string;
  starts_at: string;
  ends_at: string;
  hours: number;
  status: string;
  reason: string;
  coverage: Record<string, unknown>;
  decided_at: string | null;
}

export interface CustomDomainRow {
  id: string;
  domain: string;
  status: string;
  environment_id: string | null;
  ownership_record_name: string;
  cname_target: string;
  last_checked_at: string | null;
  verified_at: string | null;
  activated_at: string | null;
  reverify_due_at: string | null;
  failure_reason: string;
  created_at: string | null;
  instructions: {
    ownership: { record_type: string; name: string; value: string };
    routing: { record_type: string; name: string; value: string };
  };
}

export interface DomainCheckObservations {
  /** Resolver names that answered, in the order the platform asked them. */
  names: string[];
  /** What the platform expected to find, plus why, when there is a reason. */
  expected: Record<string, unknown>;
  /** What DNS actually returned, including the error when the query failed. */
  observed: Record<string, unknown>;
}

export interface DomainCheckRow {
  id: string;
  check_type: string;
  passed: boolean;
  /** True when every resolver that answered agreed — a split answer is not a pass. */
  consistent: boolean;
  resolvers: DomainCheckObservations | { names?: string[]; expected?: unknown; observed?: unknown };
  /** One sentence for the console; the structured detail lives in `resolvers`. */
  detail: string;
  checked_at: string | null;
}

export interface DomainVerificationResult {
  domain_id: string;
  domain: string;
  passed: boolean;
  status: string;
  reason: string;
  checks: DomainCheckRow[];
}

export interface ThemeContrastEntry {
  foreground: string;
  background: string;
  ratio: number;
  minimum: number;
  passed: boolean;
}

export interface ThemeContrastReport {
  pairs: ThemeContrastEntry[];
  failures: string[];
  passed: boolean;
}

export interface ThemeVersionRow {
  id: string;
  theme_id: string;
  version_number: number;
  payload: Record<string, unknown>;
  contrast: ThemeContrastReport | Record<string, unknown>;
  created_at: string | null;
  published_at: string | null;
}

export interface ThemeResponse {
  payload: Record<string, unknown>;
  version: ThemeVersionRow | null;
  defaults: Record<string, unknown>;
  allowed_fonts: string[];
}

export interface ThemeValidationResponse {
  valid: boolean;
  payload: Record<string, unknown>;
  contrast: ThemeContrastReport;
}

export interface BrandAssetRow {
  id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  sanitized: boolean;
  created_at: string | null;
  url: string;
}

export interface SenderDomainRow {
  id: string;
  domain: string;
  from_name: string;
  dkim_selector: string;
  status: string;
  spf_verified: boolean;
  dkim_verified: boolean;
  dmarc_verified: boolean;
  /** What the tenant must publish — available before any check has run. */
  records: SenderRecordsMap;
  last_checked_at: string | null;
  verified_at: string | null;
  created_at: string | null;
}

export interface SenderRecordRow {
  id: string;
  record_type: string;
  name: string;
  expected: string;
  /** Every answer the resolver returned, raw — not a joined string. */
  observed: string[];
  passed: boolean;
  /** The parsed verdict: present, valid, reason, resolver error. */
  detail: Record<string, unknown>;
  checked_at: string | null;
}

export interface CertificateStatus {
  /** One of pending | issued | expiring | expired | failed. */
  state: string;
  issuer: string;
  serial: string;
  not_before: string | null;
  not_after: string | null;
  days_to_expiry: number | null;
  renewal_due: boolean;
  error: string;
  checked_at: string | null;
}

export interface MobileDeviceRow {
  id: string;
  user_id: string;
  platform: string;
  device_id: string;
  app_version: string;
  os_version: string;
  push_enabled: boolean;
  quiet_hours: Record<string, unknown>;
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string | null;
  tokens: Array<{
    id: string;
    platform: string;
    hint: string;
    active: boolean;
    failure_count: number;
    last_error: string;
    invalidated_at: string | null;
  }>;
}

export interface PushDeliveryRow {
  id: string;
  device_id: string;
  template: string;
  provider: string;
  status: string;
  provider_message_id: string;
  error_code: string;
  error_reason: string;
  collapse_id: string;
  created_at: string | null;
  sent_at: string | null;
}

export interface ProviderStatus {
  fcm: { enabled: boolean; configured: boolean };
  apns: { enabled: boolean; configured: boolean; use_sandbox: boolean };
}

export interface TestPushAttempt {
  device_id?: string;
  platform?: string;
  status: string;
  reason: string;
  [key: string]: unknown;
}

export interface TestPushResponse {
  attempts: TestPushAttempt[];
  note: string;
}

// -------------------------------------------------------------------- client ---

export const plat2Api = {
  // WFM ---------------------------------------------------------------------
  listForecasts: (params: { queueKey?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.queueKey !== undefined) query.set("queue_key", params.queueKey);
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    const suffix = query.toString() ? `?${query}` : "";
    return request<{ forecasts: ForecastSummary[] }>(`${WFM_BASE}/forecasts${suffix}`);
  },

  createForecast: (body: {
    queue_key: string;
    horizon_days: number;
    interval_minutes?: number | null;
    zone?: string | null;
    start_date?: string | null;
    overrides?: Record<string, unknown> | null;
  }) =>
    request<ForecastSummary>(`${WFM_BASE}/forecasts`, {
      method: "POST",
      body,
      idempotencyKey: idempotencyKey("wfm.forecast"),
    }),

  readForecast: (forecastId: string) =>
    request<ForecastDetail>(`${WFM_BASE}/forecasts/${forecastId}`, { method: "GET" }),

  readStaffing: (
    forecastId: string,
    params: {
      serviceLevel?: number;
      answerSeconds?: number;
      maxOccupancy?: number;
      shrinkage?: number;
      averageHandleSeconds?: number;
      band?: string;
    } = {},
  ) => {
    const query = new URLSearchParams({ forecast_id: forecastId });
    if (params.serviceLevel !== undefined) query.set("service_level", String(params.serviceLevel));
    if (params.answerSeconds !== undefined) query.set("answer_seconds", String(params.answerSeconds));
    if (params.maxOccupancy !== undefined) query.set("max_occupancy", String(params.maxOccupancy));
    if (params.shrinkage !== undefined) query.set("shrinkage", String(params.shrinkage));
    if (params.averageHandleSeconds !== undefined) {
      query.set("average_handle_seconds", String(params.averageHandleSeconds));
    }
    if (params.band !== undefined) query.set("band", params.band);
    return request<StaffingPlan>(`${WFM_BASE}/staffing?${query}`);
  },

  readAccuracy: (params: { queueKey?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.queueKey !== undefined) query.set("queue_key", params.queueKey);
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    const suffix = query.toString() ? `?${query}` : "";
    return request<AccuracyResponse>(`${WFM_BASE}/accuracy${suffix}`);
  },

  historyCounts: (params: { queueKey?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.queueKey !== undefined) query.set("queue_key", params.queueKey);
    const suffix = query.toString() ? `?${query}` : "";
    return request<HistoryCounts>(`${WFM_BASE}/history/counts${suffix}`);
  },

  listTemplates: () => request<{ templates: ShiftTemplateRow[] }>(`${WFM_BASE}/templates`),

  listLaborRules: () => request<{ rules: LaborRuleRow[] }>(`${WFM_BASE}/labor-rules`),

  listSchedules: (limit = 20) =>
    request<{ schedules: ScheduleRow[] }>(`${WFM_BASE}/schedules?limit=${limit}`),

  readSchedule: (scheduleId: string) =>
    request<ScheduleDetail>(`${WFM_BASE}/schedules/${scheduleId}`),

  solveSchedule: (
    scheduleId: string,
    body: {
      schedule_id: string;
      employee_ids: string[];
      template_ids: string[];
      auto_record?: boolean;
      allow_shortfall?: boolean;
      note?: string;
    },
  ) =>
    request<SolveReport>(`${WFM_BASE}/schedules/${scheduleId}/solve`, {
      method: "POST",
      body,
      idempotencyKey: idempotencyKey("wfm.solve"),
    }),

  /** `version_id` is required by the route: publishing is always of a named version. */
  publishSchedule: (
    scheduleId: string,
    body: { version_id: string; min_coverage_ratio?: number },
  ) =>
    request<PublishResult>(`${WFM_BASE}/schedules/${scheduleId}/publish`, {
      method: "POST",
      body,
      idempotencyKey: idempotencyKey("wfm.publish"),
    }),

  diffSchedule: (scheduleId: string, beforeVersionId: string, afterVersionId: string) => {
    const query = new URLSearchParams({
      before_version_id: beforeVersionId,
      after_version_id: afterVersionId,
    });
    return request<Record<string, unknown>>(
      `${WFM_BASE}/schedules/${scheduleId}/diff?${query}`,
    );
  },

  /** `statuses` is a comma-separated query value on this route, not repeated params. */
  listTimeOff: (statuses: string[] = ["requested"], userId?: string) => {
    const query = new URLSearchParams({ statuses: statuses.join(",") });
    if (userId) query.set("user_id", userId);
    return request<{ requests: TimeOffRow[] }>(`${WFM_BASE}/time-off?${query}`);
  },

  decideTimeOff: (requestId: string, body: { approved: boolean; note?: string }, key?: string) =>
    request<TimeOffRow>(`${WFM_BASE}/time-off/${requestId}/decide`, {
      method: "POST",
      body,
      idempotencyKey: key ?? idempotencyKey("wfm.timeoff.decide"),
    }),

  // White-label -------------------------------------------------------------
  listDomains: () =>
    request<{ domains: CustomDomainRow[]; count: number }>(`${WHITELABEL_BASE}/domains`),

  /** Returns the domain plus the ownership token — shown once, never again. */
  createDomain: (body: { domain: string; environment_id?: string | null }, key?: string) =>
    request<CustomDomainRow & { ownership_token: string; ownership_token_notice: string }>(
      `${WHITELABEL_BASE}/domains`,
      { method: "POST", body, idempotencyKey: key ?? idempotencyKey("wl.domain.create") },
    ),

  verifyDomain: (domainId: string, token?: string, key?: string) =>
    request<DomainVerificationResult>(`${WHITELABEL_BASE}/domains/${domainId}/verify`, {
      method: "POST",
      body: token ? { token } : {},
      idempotencyKey: key ?? idempotencyKey("wl.domain.verify"),
    }),

  domainChecks: (domainId: string) =>
    request<{ checks: DomainCheckRow[] }>(`${WHITELABEL_BASE}/domains/${domainId}/checks`),

  readCertificate: (domainId: string) =>
    request<CertificateStatus>(`${WHITELABEL_BASE}/domains/${domainId}/certificate`),

  readTheme: () => request<ThemeResponse>(`${WHITELABEL_BASE}/theme`),

  validateTheme: (payload: Record<string, unknown>) =>
    request<ThemeValidationResponse>(`${WHITELABEL_BASE}/theme/validate`, {
      method: "POST",
      body: { payload },
    }),

  publishTheme: (
    payload: Record<string, unknown>,
    options: { activate?: boolean; name?: string; key?: string } = {},
  ) =>
    request<ThemeVersionRow>(`${WHITELABEL_BASE}/theme`, {
      method: "POST",
      body: {
        payload,
        activate: options.activate ?? true,
        name: options.name ?? "",
      },
      idempotencyKey: options.key ?? idempotencyKey("wl.theme.publish"),
    }),

  listAssets: () => request<{ assets: BrandAssetRow[] }>(`${WHITELABEL_BASE}/assets`),

  listSenderDomains: () =>
    request<{ sender_domains: SenderDomainRow[] }>(`${WHITELABEL_BASE}/email-domains`),

  senderRecords: (senderId: string) =>
    request<{ records: SenderRecordsMap; checks: SenderRecordRow[] }>(
      `${WHITELABEL_BASE}/email-domains/${senderId}/records`,
    ),

  verifySender: (senderId: string, key?: string) =>
    request<Record<string, unknown>>(`${WHITELABEL_BASE}/email-domains/${senderId}/verify`, {
      method: "POST",
      body: {},
      idempotencyKey: key ?? idempotencyKey("wl.sender_domain.verify"),
    }),

  readWidget: () =>
    request<{
      config: Record<string, unknown>;
      loader_script: string;
      content_security_policy: string;
      frame_ancestors: string;
      cache_seconds: number;
    }>(`${WHITELABEL_BASE}/widget`),

  tlsStatus: (domain: string) =>
    request<CertificateStatus & { domain_status: string }>(
      `${TLS_BASE}/status?${new URLSearchParams({ domain })}`,
    ),

  // Mobile ------------------------------------------------------------------
  listDevices: () => request<{ devices: MobileDeviceRow[]; count: number }>(`${MOBILE_BASE}/devices`),

  listDeliveries: (limit?: number) =>
    request<{ deliveries: PushDeliveryRow[]; summary: Record<string, number> }>(
      `${MOBILE_BASE}/deliveries${limit ? `?limit=${limit}` : ""}`,
    ),

  providerStatus: () => request<ProviderStatus>(`${MOBILE_BASE}/providers`),

  sendTestPush: (
    body: { template: string; title: string; body: string; urgent: boolean },
    key?: string,
  ) =>
    request<TestPushResponse>(`${MOBILE_BASE}/push/test`, {
      method: "POST",
      body,
      idempotencyKey: key ?? idempotencyKey("mobile.push.test"),
    }),
};
