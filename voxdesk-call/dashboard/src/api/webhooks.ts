/**
 * dashboard/src/api/webhooks.ts
 * Webhook endpoints, delivery log, secret rotation and replay.
 *
 * Route source: app/api/webhook_lifecycle_routes.py — `APIRouter(prefix="/api/webhooks")`.
 * The response schemas in contracts/openapi.json are `any` for this router, so
 * the types below mirror the serializers in that module literally:
 *   `_out()`      → endpoint document (`endpoints` envelope on list)
 *   `_delivery()` → delivery document (`deliveries` envelope on list)
 *
 * Notes that matter for the UI:
 *   • `headers` comes back masked (`"***"`) — the backend never re-discloses them.
 *   • `rotate-secret` returns the new secret exactly once (`secret`, `secret_version`).
 *   • Replay is `POST /{id}/deliveries/{delivery_id}/retry`; `POST /{id}/replay`
 *     takes a `{delivery_id}` body and does the same thing.
 */
import { client } from './client';

export interface RetryPolicy {
  max_attempts: number;
  backoff_seconds: number;
  max_backoff_seconds: number;
}

export interface WebhookEndpoint {
  id: string;
  tenant_id: string;
  environment_id: string | null;
  url: string;
  description: string;
  events: string[];
  is_active: boolean;
  retry_policy: RetryPolicy | Record<string, unknown> | null;
  timeout_seconds: number;
  headers: Record<string, string>;
  secret_version: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface WebhookEndpointList {
  endpoints: WebhookEndpoint[];
  total: number;
  limit: number;
  offset: number;
}

export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event_id: string | null;
  status: string;
  http_status: number | null;
  attempts: number;
  attempt_limit: number;
  next_retry_at: string | null;
  created_at: string | null;
  delivered_at: string | null;
  latency_ms: number | null;
  last_error_category: string | null;
  is_dlq: boolean;
  replay_count: number;
}

export interface WebhookDeliveryList {
  deliveries: WebhookDelivery[];
  total: number;
  limit: number;
  offset: number;
}

export interface WebhookDeliveryDetail {
  delivery: WebhookDelivery;
  response_body: string;
}

export interface WebhookSecretRotation {
  id: string;
  secret_version: number;
  /** Returned once; the previous secret stops verifying after rotation. */
  secret: string;
}

export interface WebhookCreateInput {
  url: string;
  description?: string;
  secret?: string;
  events?: string[];
  is_active?: boolean;
  retry_policy?: RetryPolicy;
  headers?: Record<string, string>;
  timeout_seconds?: number;
  enabled_events_only?: boolean;
}

export type WebhookUpdateInput = Partial<Omit<WebhookCreateInput, 'secret' | 'enabled_events_only'>>;

export interface WebhookEventCatalog {
  [key: string]: unknown;
}

export function listWebhooks(params: { limit?: number; offset?: number; isActive?: boolean | null; eventType?: string | null; search?: string | null } = {}): Promise<WebhookEndpointList> {
  const qs = new URLSearchParams();
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
  if (params.isActive !== undefined && params.isActive !== null) qs.set('is_active', String(params.isActive));
  if (params.eventType) qs.set('event_type', params.eventType);
  if (params.search) qs.set('search', params.search);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return client.get<WebhookEndpointList>(`/api/webhooks${suffix}`);
}

export function getWebhook(endpointId: string): Promise<WebhookEndpoint> {
  return client.get<WebhookEndpoint>(`/api/webhooks/${encodeURIComponent(endpointId)}`);
}

export function createWebhook(input: WebhookCreateInput): Promise<WebhookEndpoint> {
  return client.post<WebhookEndpoint>('/api/webhooks', input);
}

export function updateWebhook(endpointId: string, patch: WebhookUpdateInput): Promise<WebhookEndpoint> {
  return client.patch<WebhookEndpoint>(`/api/webhooks/${encodeURIComponent(endpointId)}`, patch);
}

export function deleteWebhook(endpointId: string): Promise<{ id: string; deleted: boolean }> {
  return client.del<{ id: string; deleted: boolean }>(`/api/webhooks/${encodeURIComponent(endpointId)}`);
}

export function enableWebhook(endpointId: string): Promise<WebhookEndpoint> {
  return client.post<WebhookEndpoint>(`/api/webhooks/${encodeURIComponent(endpointId)}/enable`);
}

export function disableWebhook(endpointId: string): Promise<WebhookEndpoint> {
  return client.post<WebhookEndpoint>(`/api/webhooks/${encodeURIComponent(endpointId)}/disable`);
}

export function listDeliveries(
  endpointId: string,
  params: { status?: string | null; eventType?: string | null; limit?: number; offset?: number } = {},
): Promise<WebhookDeliveryList> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (params.eventType) qs.set('event_type', params.eventType);
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return client.get<WebhookDeliveryList>(`/api/webhooks/${encodeURIComponent(endpointId)}/deliveries${suffix}`);
}

export function getDelivery(endpointId: string, deliveryId: string): Promise<WebhookDeliveryDetail> {
  return client.get<WebhookDeliveryDetail>(
    `/api/webhooks/${encodeURIComponent(endpointId)}/deliveries/${encodeURIComponent(deliveryId)}`,
  );
}

/** Re-send one delivery through the durable ledger; acknowledged sends are refused. */
export function retryDelivery(endpointId: string, deliveryId: string): Promise<WebhookDelivery & { success: boolean }> {
  return client.post<WebhookDelivery & { success: boolean }>(
    `/api/webhooks/${encodeURIComponent(endpointId)}/deliveries/${encodeURIComponent(deliveryId)}/retry`,
  );
}

/** Bulk replay; the route takes the delivery id in the body. */
export function replayWebhook(endpointId: string, deliveryId: string): Promise<WebhookDelivery & { success: boolean }> {
  return client.post<WebhookDelivery & { success: boolean }>(
    `/api/webhooks/${encodeURIComponent(endpointId)}/replay`,
    { delivery_id: deliveryId },
  );
}

export function listDlq(endpointId: string, limit = 50, offset = 0): Promise<WebhookDeliveryList> {
  return client.get<WebhookDeliveryList>(
    `/api/webhooks/${encodeURIComponent(endpointId)}/dlq?limit=${limit}&offset=${offset}`,
  );
}

export function replayAllDlq(endpointId: string): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>(`/api/webhooks/${encodeURIComponent(endpointId)}/dlq/replay-all`);
}

export function rotateSecret(endpointId: string, secret?: string): Promise<WebhookSecretRotation> {
  return client.post<WebhookSecretRotation>(
    `/api/webhooks/${encodeURIComponent(endpointId)}/rotate-secret`,
    secret ? { secret } : {},
  );
}

export function sendTestEvent(endpointId: string, eventType = 'webhook.test'): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>(`/api/webhooks/${encodeURIComponent(endpointId)}/test`, {
    event_type: eventType,
    payload: {},
  });
}

export function webhookHealth(): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>('/api/webhooks/health');
}

export function webhookEventCategories(): Promise<WebhookEventCatalog> {
  return client.get<WebhookEventCatalog>('/api/webhooks/events/categories');
}

export function allowedEvents(): Promise<WebhookEventCatalog> {
  return client.get<WebhookEventCatalog>('/api/webhooks/events/allowed');
}
