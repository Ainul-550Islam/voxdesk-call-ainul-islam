/**
 * dashboard/src/api/audit.ts
 * Audit-log listing, filtering and export.
 *
 * Route sources (verified):
 *   app/api/audit_trail_routes.py → GET /api/audit-trail/events  (legacy path,
 *       delegates to app/api/v1/audit_routes.py::query_audit_events)
 *   app/api/security_audit_routes.py → GET /api/security/audit/events (same handler)
 *
 * The handler accepts `limit` (1..500), `offset`, `start`/`end` (window capped at
 * 366 days), `event_type`, `actor_id`, `target_user_id`, `environment_id`,
 * `resource_type`, `resource_id` and `result`
 * (one of success/failure/denied/pending/error). Those constraints are encoded
 * here so a page cannot send a request the server will reject with a 422.
 *
 * There is no server-side CSV route for audit events: the export is built in the
 * browser from the pages the operator already loaded, which is why
 * `exportEventsCsv` takes the rows rather than a query.
 */
import { client } from './client';

export const AUDIT_RESULTS = ['success', 'failure', 'denied', 'pending', 'error'] as const;
export type AuditResult = (typeof AUDIT_RESULTS)[number];

export interface AuditEvent {
  id: string;
  action: string;
  event_type: string;
  created_at: string;
  actor_id?: string | null;
  actor_type?: string;
  actor_email?: string;
  target_user_id?: string | null;
  environment_id?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  request_id?: string | null;
  result?: string;
  ip_address?: string;
  user_agent?: string;
  detail?: Record<string, unknown>;
}

export interface AuditPage {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  event_types: string[];
}

export interface AuditFilters {
  limit?: number;
  offset?: number;
  start?: string | null;
  end?: string | null;
  eventType?: string | null;
  actorId?: string | null;
  targetUserId?: string | null;
  environmentId?: string | null;
  resourceType?: string | null;
  resourceId?: string | null;
  result?: AuditResult | null;
}

export const AUDIT_PAGE_SIZE_MAX = 500;
const MAX_WINDOW_DAYS = 366;

function buildQuery(filters: AuditFilters): string {
  const qs = new URLSearchParams();
  const limit = Math.min(Math.max(filters.limit ?? 100, 1), AUDIT_PAGE_SIZE_MAX);
  qs.set('limit', String(limit));
  qs.set('offset', String(Math.max(filters.offset ?? 0, 0)));
  if (filters.start) qs.set('start', filters.start);
  if (filters.end) qs.set('end', filters.end);
  if (filters.eventType) qs.set('event_type', filters.eventType);
  if (filters.actorId) qs.set('actor_id', filters.actorId);
  if (filters.targetUserId) qs.set('target_user_id', filters.targetUserId);
  if (filters.environmentId) qs.set('environment_id', filters.environmentId);
  if (filters.resourceType) qs.set('resource_type', filters.resourceType);
  if (filters.resourceId) qs.set('resource_id', filters.resourceId);
  if (filters.result) qs.set('result', filters.result);
  return qs.toString();
}

export function listAuditEvents(filters: AuditFilters = {}): Promise<AuditPage> {
  return client.get<AuditPage>(`/api/audit-trail/events?${buildQuery(filters)}`);
}

export function listSecurityAuditEvents(filters: AuditFilters = {}): Promise<AuditPage> {
  return client.get<AuditPage>(`/api/security/audit/events?${buildQuery(filters)}`);
}

/**
 * The server rejects a window longer than 366 days with a 422; validate locally
 * first so the operator gets an explanation instead of a generic failure.
 */
export function validateWindow(start: string | null, end: string | null): string | null {
  if (!start || !end) return null;
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return 'Both dates must be valid ISO timestamps.';
  if (startMs >= endMs) return 'The start must be earlier than the end.';
  if (endMs - startMs > MAX_WINDOW_DAYS * 86_400_000) return `The window may not exceed ${MAX_WINDOW_DAYS} days.`;
  return null;
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

const CSV_COLUMNS: Array<keyof AuditEvent> = [
  'created_at',
  'event_type',
  'action',
  'result',
  'actor_email',
  'actor_id',
  'actor_type',
  'target_user_id',
  'environment_id',
  'resource_type',
  'resource_id',
  'request_id',
  'ip_address',
  // Kept in the export: an auditor investigating a session needs the client the
  // request came from, and this is the only place it is captured.
  'user_agent',
];

/** Serialise loaded audit rows to RFC 4180 CSV (CRLF, quoted, escaped quotes). */
export function exportEventsCsv(events: AuditEvent[]): string {
  const header = ['created_at', 'event_type', 'action', 'result', 'actor_email', 'actor_id', 'actor_type', 'target_user_id', 'environment_id', 'resource_type', 'resource_id', 'request_id', 'ip_address', 'user_agent', 'detail'];
  const lines = [header.join(',')];
  for (const event of events) {
    const cells = CSV_COLUMNS.map((column) => csvCell(event[column]));
    cells.push(csvCell(event.detail ?? {}));
    lines.push(cells.join(','));
  }
  return `${lines.join('\r\n')}\r\n`;
}
