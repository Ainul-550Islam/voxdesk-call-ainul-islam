/** Push status and self-test.
 *
 * `test()` exists so the "notifications aren't arriving" support conversation can
 * be two taps instead of an investigation: it pushes to the caller's own devices
 * and returns the per-device outcome, including the provider error code.
 */

import type { DeliveryAttempt, DeliveryList, ProviderStatus } from "./types.js";
import type { VoxDeskClient } from "./client.js";

export interface PushTestInput {
  /** Template name; `push_test` is the only one the API accepts for a self-test. */
  template?: string;
  title?: string;
  body?: string;
  /** Bypass quiet hours. Use only for an explicit user-initiated test. */
  urgent?: boolean;
}

export interface PushTestResult {
  attempts: DeliveryAttempt[];
  sent: number;
  failed: number;
  suppressed: number;
}

export class PushApi {
  constructor(private readonly client: VoxDeskClient) {}

  async deliveries(options: { limit?: number; status?: string } = {}): Promise<DeliveryList> {
    const query: Record<string, string | number | undefined> = {};
    if (options.limit !== undefined) query.limit = options.limit;
    if (options.status !== undefined) query.status = options.status;
    return this.client.request<DeliveryList>("/api/mobile/deliveries", { query });
  }

  async providers(): Promise<ProviderStatus> {
    return this.client.request<ProviderStatus>("/api/mobile/providers");
  }

  async test(input: PushTestInput = {}): Promise<PushTestResult> {
    const body: Record<string, unknown> = { urgent: input.urgent ?? false };
    if (input.template) body.template = input.template;
    if (input.title) body.title = input.title;
    if (input.body) body.body = input.body;
    return this.client.request<PushTestResult>("/api/mobile/push/test", {
      method: "POST",
      body,
    });
  }
}

/**
 * Should a notification be shown now, on this device?
 *
 * The server applies quiet hours when it sends, so this is only for the *local*
 * notification case (a timer, a foreground reminder) where the server is not in the
 * loop. It is deliberately a pure function of the window and the clock so the same
 * answer can be unit-tested with a fixed time.
 */
export function inQuietHours(
  quietHours: { start?: string; end?: string; enabled?: boolean } | null | undefined,
  now: Date,
  timeZone: string,
): boolean {
  if (!quietHours || !quietHours.start || !quietHours.end || quietHours.enabled === false) {
    return false;
  }
  const pattern = /^([01]\d|2[0-3]):([0-5]\d)$/;
  if (!pattern.test(quietHours.start) || !pattern.test(quietHours.end)) {
    // A malformed window must never silence notifications.
    return false;
  }
  const minutes = localMinutes(now, timeZone);
  if (minutes === null) {
    return false;
  }
  const start = toMinutes(quietHours.start);
  const end = toMinutes(quietHours.end);
  if (start === end) {
    return false;
  }
  return start < end ? minutes >= start && minutes < end : minutes >= start || minutes < end;
}

function toMinutes(value: string): number {
  const [hour, minute] = value.split(":");
  return Number(hour) * 60 + Number(minute ?? 0);
}

/** Minutes since local midnight in `timeZone`, or null for an unknown zone. */
export function localMinutes(now: Date, timeZone: string): number | null {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(now);
    const hour = parts.find((part) => part.type === "hour")?.value;
    const minute = parts.find((part) => part.type === "minute")?.value;
    if (hour === undefined || minute === undefined) {
      return null;
    }
    // `24` is how some ICU builds render midnight with hour12: false.
    return (Number(hour) % 24) * 60 + Number(minute);
  } catch {
    return null;
  }
}
