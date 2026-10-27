import { describe, expect, it } from "vitest";

import { VoxDeskClient } from "../src/client.js";
import { DeviceApi, validateQuietHours } from "../src/devices.js";
import { ApiError } from "../src/errors.js";
import { makeFetch } from "./harness.js";

function subject(responses: Parameters<typeof makeFetch>[0]) {
  const { fetchImpl, requests } = makeFetch(responses);
  const client = new VoxDeskClient({ baseUrl: "https://acme.example", fetchImpl });
  return { api: new DeviceApi(client), requests };
}

const device = {
  id: "dev-1",
  user_id: "user-1",
  platform: "ios" as const,
  device_id: "hardware-abc",
  app_version: "1.0.0",
  os_version: "18.1",
  push_enabled: true,
  quiet_hours: { start: "22:00", end: "07:00", timezone: "Asia/Dhaka" },
  last_seen_at: null,
  revoked_at: null,
  created_at: null,
  tokens: [],
};

describe("DeviceApi", () => {
  it("lists devices from the envelope", async () => {
    const { api, requests } = subject([{ body: { devices: [device], count: 1 } }]);
    await expect(api.list()).resolves.toHaveLength(1);
    expect(requests[0]?.url).toBe("https://acme.example/api/mobile/devices");
  });

  it("registers with snake_case wire fields and no userId", async () => {
    const { api, requests } = subject([{ body: { ...device, created: true } }]);
    await api.register({
      platform: "ios",
      deviceId: "hardware-abc",
      appVersion: "1.0.0",
      osVersion: "18.1",
      token: "push-token-1",
    });
    expect(requests[0]?.body).toEqual({
      platform: "ios",
      device_id: "hardware-abc",
      app_version: "1.0.0",
      os_version: "18.1",
      push_enabled: true,
      token: "push-token-1",
    });
    expect(requests[0]?.body as Record<string, unknown>).not.toHaveProperty("user_id");
  });

  it("omits the token when the caller did not get one yet", async () => {
    const { api, requests } = subject([{ body: { ...device, created: false } }]);
    await api.register({ platform: "android", deviceId: "x" });
    expect(requests[0]?.body).not.toHaveProperty("token");
    expect(requests[0]?.body).toMatchObject({ platform: "android", app_version: "", os_version: "" });
  });

  it("sends quiet hours as a nested object, and null to clear them", async () => {
    const { api, requests } = subject([{ body: device }, { body: device }]);
    await api.setQuietHours("dev-1", { start: "22:00", end: "07:00", timezone: "Asia/Dhaka" });
    await api.setQuietHours("dev-1", null);
    expect(requests[0]?.body).toEqual({
      quiet_hours: { start: "22:00", end: "07:00", timezone: "Asia/Dhaka" },
    });
    expect(requests[1]?.body).toEqual({ quiet_hours: null });
  });

  it("treats a 404 on revoke as success — the device is gone either way", async () => {
    const { api } = subject([
      { status: 404, raw: JSON.stringify({ detail: { code: "device_not_found", message: "gone" } }) },
    ]);
    await expect(api.revoke("dev-1")).resolves.toBeUndefined();
  });

  it("still raises on a real failure while revoking", async () => {
    const { api } = subject([
      { status: 500, raw: JSON.stringify({ detail: { code: "internal", message: "boom" } }) },
    ]);
    await expect(api.revoke("dev-1")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("validateQuietHours", () => {
  it("accepts a window that crosses midnight", () => {
    expect(validateQuietHours({ start: "22:00", end: "07:00", timezone: "Asia/Dhaka" })).toEqual({
      ok: true,
    });
  });

  it("rejects a zero-length window", () => {
    expect(validateQuietHours({ start: "09:00", end: "09:00", timezone: "Asia/Dhaka" })).toEqual({
      ok: false,
      reason: "a quiet window must have a non-zero length",
    });
  });

  it("rejects a window longer than the ceiling", () => {
    const result = validateQuietHours({ start: "06:00", end: "20:00", timezone: "Asia/Dhaka" });
    expect(result.ok).toBe(false);
    expect(result).toMatchObject({ reason: "a quiet window may be at most 12 hours" });
  });

  it("honours a deployment-specific ceiling", () => {
    expect(
      validateQuietHours({ start: "06:00", end: "20:00", timezone: "Asia/Dhaka" }, 14).ok,
    ).toBe(true);
  });

  it("rejects malformed times and a missing zone", () => {
    expect(validateQuietHours({ start: "9am", end: "07:00", timezone: "UTC" }).ok).toBe(false);
    expect(validateQuietHours({ start: "22:00", end: "07:00", timezone: "" }).ok).toBe(false);
  });
});
