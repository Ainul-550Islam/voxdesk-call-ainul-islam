/** Device and push-token lifecycle, as the host app uses it.
 *
 * The important behaviours are the ones an app would get wrong by hand:
 *
 * * `register` is **idempotent** and therefore safe to call on every launch: the
 *   server scopes a row by (tenant, user, platform, device id) and clears a
 *   previous revocation, which is exactly what a reinstall is.
 * * `registerToken` sends the token once and forgets it. The API never returns it,
 *   so there is nothing to store on the device beyond what the OS already holds.
 * * `revoke` is called on sign-out. A device that stays registered after sign-out
 *   keeps receiving another person's schedule notifications.
 */

import { ApiError } from "./errors.js";
import type { Device, Platform, QuietHours, RegisterDeviceInput } from "./types.js";
import type { VoxDeskClient } from "./client.js";

export interface DeviceListResponse {
  devices: Device[];
  count: number;
}

export class DeviceApi {
  constructor(private readonly client: VoxDeskClient) {}

  async list(): Promise<Device[]> {
    const response = await this.client.request<DeviceListResponse>("/api/mobile/devices");
    return response.devices;
  }

  async register(input: RegisterDeviceInput): Promise<Device & { created: boolean }> {
    const body: Record<string, unknown> = {
      platform: input.platform,
      device_id: input.deviceId,
      app_version: input.appVersion ?? "",
      os_version: input.osVersion ?? "",
      push_enabled: input.pushEnabled ?? true,
    };
    if (input.quietHours) {
      body.quiet_hours = input.quietHours;
    }
    if (input.token) {
      body.token = input.token;
    }
    return this.client.request<Device & { created: boolean }>("/api/mobile/devices", {
      method: "POST",
      body,
    });
  }

  async registerToken(deviceId: string, token: string): Promise<void> {
    await this.client.request<unknown>(`/api/mobile/devices/${deviceId}/token`, {
      method: "POST",
      body: { token },
    });
  }

  async setQuietHours(deviceId: string, quietHours: QuietHours | null): Promise<Device> {
    return this.client.request<Device>(`/api/mobile/devices/${deviceId}/quiet-hours`, {
      method: "POST",
      body: { quiet_hours: quietHours },
    });
  }

  async revoke(deviceId: string): Promise<void> {
    try {
      await this.client.request<void>(`/api/mobile/devices/${deviceId}`, { method: "DELETE" });
    } catch (error) {
      // Already gone is the desired end state, so a 404 is not a failure here.
      if (error instanceof ApiError && error.status === 404) {
        return;
      }
      throw error;
    }
  }
}

/**
 * Validate a quiet-hours window locally before sending it.
 *
 * The server is the authority (`PUSH_QUIET_HOURS_MAX_SPAN` is a deployment setting),
 * but a round trip to learn "your window is 14 hours and the maximum is 12" is a
 * bad experience in a settings screen. The client enforces the same shape and the
 * same default ceiling; the server's answer still wins if they disagree.
 */
export function validateQuietHours(
  quietHours: QuietHours,
  maxSpanHours = 12,
): { ok: true } | { ok: false; reason: string } {
  const pattern = /^([01]\d|2[0-3]):([0-5]\d)$/;
  if (!pattern.test(quietHours.start) || !pattern.test(quietHours.end)) {
    return { ok: false, reason: "times must be HH:MM" };
  }
  const toMinutes = (value: string): number => {
    const [hour, minute] = value.split(":");
    return Number(hour) * 60 + Number(minute ?? 0);
  };
  const span = (toMinutes(quietHours.end) - toMinutes(quietHours.start) + 24 * 60) % (24 * 60);
  if (span === 0) {
    return { ok: false, reason: "a quiet window must have a non-zero length" };
  }
  if (span > maxSpanHours * 60) {
    return { ok: false, reason: `a quiet window may be at most ${maxSpanHours} hours` };
  }
  if (!quietHours.timezone) {
    return { ok: false, reason: "a timezone is required" };
  }
  return { ok: true };
}

export const PLATFORMS: readonly Platform[] = ["ios", "android"];
