/**
 * dashboard/src/api/analytics.ts
 * Call, conversion, usage and ROI analytics.
 *
 * Route sources (verified):
 *   app/api/analytics_routes.py → prefix `/api/analytics`
 *       GET /overview | /calls | /conversion | /usage
 *       every reader except /usage takes `range` (preset) or `start`+`end`
 *       (inclusive **local dates**, resolved server-side in the tenant TZ)
 *   app/api/roi_routes.py → prefix `/api/roi`
 *       GET /kpis, /baselines, /measurement/{period}, /compare, /results/{id}
 *       POST /calculate, /kpis, /kpis/{id}/evaluate, /costs, /outcomes, /baselines
 *
 * The window is never computed in the browser. Nested documents (`calls`,
 * `conversion`, `operations`, `integrations`) are typed from the literal dicts
 * built in `overview()` rather than a wildcard, so a field rename breaks `tsc`
 * instead of silently rendering `undefined`.
 */
import { client } from './client';

export const ANALYTICS_PRESETS = [
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'last_7_days', label: 'Last 7 days' },
  { value: 'last_30_days', label: 'Last 30 days' },
  { value: 'this_month', label: 'This month' },
  { value: 'previous_month', label: 'Previous month' },
] as const;

export type AnalyticsPreset = (typeof ANALYTICS_PRESETS)[number]['value'];

/** `WindowOut` */
export interface AnalyticsWindow {
  /** Human label, e.g. "Last 7 days". */
  label: string;
  /** ISO-8601 UTC instant, inclusive. */
  start: string;
  /** ISO-8601 UTC instant, exclusive. */
  end: string;
  timezone: string;
  days: number;
}

/** `_call_totals()` — one conditional-aggregation query. */
export interface CallTotals {
  total: number;
  answered: number;
  missed: number;
  failed: number;
  booked: number;
  transferred: number;
  inbound: number;
  outbound: number;
  eligible: number;
  total_seconds: number;
  /** Added by `/calls`. */
  minutes?: number;
  /** Added by `/calls`. */
  average_seconds?: number;
}

/** `_rates()` — every rate carries its own denominator server-side. */
export interface CallRates {
  answer_rate: number;
  booking_rate: number;
  transfer_rate: number;
  failure_rate: number;
}

export interface OverviewCalls {
  total: number;
  answered: number;
  missed: number;
  failed: number;
  inbound: number;
  outbound: number;
  transferred: number;
  minutes: number;
}

export interface OverviewConversion {
  eligible_calls: number;
  booked: number;
  leads_created: number;
  leads_qualified: number;
  appointments: number;
  appointments_cancelled: number;
  appointments_no_show: number;
}

export interface OverviewOperations extends CallRates {
  average_call_seconds: number;
}

export interface OverviewIntegrations {
  crm_sync_failures: number;
  calendar_failures: number;
}

/** Current-period usage snapshot; absent (not zeroed) without billing read. */
export interface UsageSnapshot {
  billing_period?: string;
  period_start?: string;
  period_end?: string;
  plan_code?: string;
  currency?: string;
  estimated_overage_cents?: number;
  metrics?: UsageMetric[];
  [key: string]: unknown;
}

export interface OverviewAnalytics {
  window: AnalyticsWindow;
  calls: OverviewCalls;
  conversion: OverviewConversion;
  operations: OverviewOperations;
  integrations: OverviewIntegrations;
  usage?: UsageSnapshot | null;
}

export interface SeriesPoint {
  date: string;
  answered: number;
  missed: number;
  failed: number;
  booked: number;
  transferred: number;
  minutes: number;
}

/** One bucket of `_hourly_distribution()` (always 24 entries, 0–23). */
export interface HourBucket {
  hour: number;
  total: number;
  missed: number;
}

export interface CallAnalytics {
  window: AnalyticsWindow;
  totals: CallTotals;
  by_status: { answered: number; missed: number; failed: number; other: number };
  by_direction: { inbound: number; outbound: number };
  by_hour: HourBucket[];
  series: SeriesPoint[];
}

/** One funnel stage; `note` states the denominator. */
export interface FunnelStage {
  stage: string;
  count: number;
  note?: string;
}

export interface ConversionRates extends CallRates {
  call_to_lead_rate: number;
  lead_to_appointment_rate: number;
  appointment_kept_rate: number;
}

export interface ConversionAnalytics {
  window: AnalyticsWindow;
  funnel: FunnelStage[];
  rates: ConversionRates;
}

/** One row of `UsageAnalyticsOut.metrics`. */
export interface UsageMetric {
  metric: string;
  unit: string;
  included: number;
  used: number;
  remaining: number;
  overage: number;
  percent_used: number;
  overage_cents: number;
}

export interface UsageAnalytics {
  billing_period: string;
  period_start: string;
  period_end: string;
  plan_code: string;
  metrics: UsageMetric[];
  estimated_overage_cents: number;
  currency: string;
}

export interface RoiKpi {
  id: string;
  [key: string]: unknown;
}

export interface WindowQuery {
  /** A named preset (`?range=`). */
  range?: AnalyticsPreset;
  /** Inclusive local start date, `YYYY-MM-DD`. */
  start?: string;
  /** Inclusive local end date, `YYYY-MM-DD`. */
  end?: string;
}

export function windowQuery(query: WindowQuery = {}): string {
  const qs = new URLSearchParams();
  if (query.start) qs.set('start', query.start);
  if (query.end) qs.set('end', query.end);
  if (query.range) qs.set('range', query.range);
  const suffix = qs.toString();
  return suffix ? `?${suffix}` : '';
}

/** Percentages are already 0–100 floats from the server. */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(1)}%`;
}

export function getAnalyticsOverview(query: WindowQuery = {}): Promise<OverviewAnalytics> {
  return client.get<OverviewAnalytics>(`/api/analytics/overview` + windowQuery(query));
}

export function getCallAnalytics(query: WindowQuery = {}): Promise<CallAnalytics> {
  return client.get<CallAnalytics>(`/api/analytics/calls` + windowQuery(query));
}

export function getConversionAnalytics(query: WindowQuery = {}): Promise<ConversionAnalytics> {
  return client.get<ConversionAnalytics>(`/api/analytics/conversion` + windowQuery(query));
}

/**
 * Metered usage for the **current billing period** — deliberately not
 * date-filtered: usage belongs to the subscription period, not to a picker.
 */
export function getUsageAnalytics(): Promise<UsageAnalytics> {
  return client.get<UsageAnalytics>('/api/analytics/usage');
}

/* ------------------------------------------------------------------- ROI --- */

function envQuery(environmentId: string): string {
  return `?environment_id=${encodeURIComponent(environmentId)}`;
}

export function listRoiKpis(environmentId: string): Promise<{ kpis?: RoiKpi[] } | RoiKpi[]> {
  return client.get<{ kpis?: RoiKpi[] } | RoiKpi[]>(`/api/roi/kpis` + envQuery(environmentId));
}

export function listRoiBaselines(environmentId: string): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>(`/api/roi/baselines` + envQuery(environmentId));
}

export function getRoiMeasurement(period: string, environmentId: string): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>(
    `/api/roi/measurement/${encodeURIComponent(period)}` + envQuery(environmentId),
  );
}

export function compareRoiPeriods(
  firstPeriod: string,
  secondPeriod: string,
  environmentId: string,
): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams({
    first_period: firstPeriod,
    second_period: secondPeriod,
    environment_id: environmentId,
  });
  return client.get<Record<string, unknown>>(`/api/roi/compare?${qs.toString()}`);
}

export function calculateRoi(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>('/api/roi/calculate', body);
}
