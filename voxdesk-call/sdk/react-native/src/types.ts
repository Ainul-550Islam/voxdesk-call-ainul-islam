/**
 * Wire types for the VoxDesk mobile API (`/api/mobile/*`).
 *
 * These mirror the Pydantic models in `app/platform_v2/mobile/router.py`. They are
 * hand-written on purpose: generated types would drift silently the first time a
 * route changes, while a hand-written file fails `tsc` the moment the SDK uses a
 * field the server does not send.
 */

export type Platform = "ios" | "android";

/** A quiet-hours window in the device's own timezone. End before start = crosses midnight. */
/** The API's error envelope: `{"detail": {"code", "message", "detail"}}`. */
export interface ApiErrorDetail {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export interface QuietHours {
  start: string; // HH:MM
  end: string; // HH:MM
  timezone: string; // IANA zone, e.g. "Asia/Dhaka"
  enabled?: boolean;
}

export interface PushTokenSummary {
  id: string;
  platform: Platform;
  /** Last six characters only. The full token is never returned by the API. */
  hint: string;
  active: boolean;
  failure_count: number;
  last_error: string;
  invalidated_at: string | null;
}

export interface Device {
  id: string;
  user_id: string;
  platform: Platform;
  device_id: string;
  app_version: string;
  os_version: string;
  push_enabled: boolean;
  quiet_hours: QuietHours | Record<string, never>;
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string | null;
  tokens: PushTokenSummary[];
}

export interface Delivery {
  id: string;
  device_id: string;
  template: string;
  provider: string;
  /** queued | sent | failed | dropped */
  status: "queued" | "sent" | "failed" | "dropped";
  provider_message_id: string;
  error_code: string;
  error_reason: string;
  collapse_id: string;
  created_at: string | null;
  sent_at: string | null;
}

export interface DeliveryList {
  deliveries: Delivery[];
  summary: Record<string, number>;
}

export interface DeliveryAttempt {
  device_id: string;
  platform: Platform;
  status: Delivery["status"];
  dedupe_key: string;
  provider_message_id: string;
  error_code: string;
  error_reason: string;
}

export interface ProviderStatus {
  fcm: { enabled: boolean; configured: boolean };
  apns: { enabled: boolean; configured: boolean; sandbox: boolean };
  quiet_hours_max_span_hours: number;
  token_gc_days: number;
}

export interface RegisterDeviceInput {
  platform: Platform;
  deviceId: string;
  appVersion?: string;
  osVersion?: string;
  pushEnabled?: boolean;
  quietHours?: QuietHours | null;
  /** FCM or APNs token. Sent once and never returned. */
  token?: string;
}

export interface ClientOptions {
  /** API base, e.g. `https://app.acme.example.com` (no trailing slash). */
  baseUrl: string;
  /** Returns the current access token, or null when signed out. */
  getAccessToken?: () => string | null | Promise<string | null>;
  /** Called on a 401 so the host app can refresh or sign out. */
  onUnauthorized?: () => void | Promise<void>;
  /** Extra headers (tenant routing, user agent). */
  headers?: Record<string, string>;
  /** Default request timeout in milliseconds. */
  timeoutMs?: number;
  /** Injectable for tests and for React Native environments without global fetch. */
  fetchImpl?: FetchLike;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  query?: Record<string, string | number | boolean | undefined>;
  /** Idempotency key for mutating requests. Strongly recommended on retries. */
  idempotencyKey?: string;
  timeoutMs?: number;
}

export interface ResponseLike {
  ok: boolean;
  status: number;
  headers?: { get(name: string): string | null };
  text(): Promise<string>;
  json(): Promise<unknown>;
}

export type FetchLike = (url: string, init?: RequestInit) => Promise<ResponseLike>;
