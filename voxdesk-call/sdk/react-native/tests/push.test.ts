import { describe, expect, it } from "vitest";

import { VoxDeskClient } from "../src/client.js";
import { PushApi, inQuietHours, localMinutes } from "../src/push.js";
import { makeFetch } from "./harness.js";

function subject(responses: Parameters<typeof makeFetch>[0]) {
  const { fetchImpl, requests } = makeFetch(responses);
  const client = new VoxDeskClient({ baseUrl: "https://acme.example", fetchImpl });
  return { api: new PushApi(client), requests };
}

describe("PushApi", () => {
  it("forwards delivery filters as query parameters", async () => {
    const { api, requests } = subject([{ body: { deliveries: [], summary: {} } }]);
    await api.deliveries({ limit: 20, status: "failed" });
    expect(requests[0]?.url).toContain("limit=20");
    expect(requests[0]?.url).toContain("status=failed");
  });

  it("omits unset filters entirely", async () => {
    const { api, requests } = subject([{ body: { deliveries: [], summary: {} } }]);
    await api.deliveries();
    expect(requests[0]?.url).toBe("https://acme.example/api/mobile/deliveries");
  });

  it("self-test defaults to non-urgent, so it cannot bypass quiet hours by accident", async () => {
    const { api, requests } = subject([
      { body: { attempts: [], sent: 0, failed: 0, suppressed: 0 } },
    ]);
    await api.test();
    expect(requests[0]?.body).toEqual({ urgent: false });
    expect(requests[0]?.method).toBe("POST");
  });

  it("passes an explicit override through", async () => {
    const { api, requests } = subject([
      { body: { attempts: [], sent: 1, failed: 0, suppressed: 0 } },
    ]);
    await api.test({ urgent: true, title: "Ping", body: "Hello" });
    expect(requests[0]?.body).toEqual({ urgent: true, title: "Ping", body: "Hello" });
  });

  it("reads provider capability without credentials", async () => {
    const { api } = subject([
      {
        body: {
          fcm: { enabled: true, configured: true },
          apns: { enabled: true, configured: false, sandbox: true },
          quiet_hours_max_span_hours: 12,
          token_gc_days: 60,
        },
      },
    ]);
    const providers = await api.providers();
    expect(providers.apns.configured).toBe(false);
    expect(JSON.stringify(providers)).not.toMatch(/key|secret|token"?\s*:/i);
  });
});

describe("quiet hours on the device clock", () => {
  const at = (iso: string) => new Date(iso);

  it("is quiet inside a window that wraps midnight", () => {
    const window = { start: "22:00", end: "07:00" };
    // Dhaka is UTC+6 with no DST, so the conversions below are exact.
    expect(inQuietHours(window, at("2026-10-26T20:00:00Z"), "Asia/Dhaka")).toBe(true); // 02:00 local
    expect(inQuietHours(window, at("2026-10-26T17:30:00Z"), "Asia/Dhaka")).toBe(true); // 23:30 local
    expect(inQuietHours(window, at("2026-10-26T04:30:00Z"), "Asia/Dhaka")).toBe(false); // 10:30 local
    expect(inQuietHours(window, at("2026-10-26T02:00:00Z"), "Asia/Dhaka")).toBe(false); // 08:00 local
  });

  it("is quiet inside a same-day window, in the device's zone rather than UTC", () => {
    const window = { start: "09:00", end: "17:00" };
    // British Summer Time ended on 25 October 2026, so from the 26th London is UTC.
    expect(inQuietHours(window, at("2026-10-26T10:00:00Z"), "Europe/London")).toBe(true);
    // 09:00Z is 09:00 local: the window has just opened.
    expect(inQuietHours(window, at("2026-10-26T09:00:00Z"), "Europe/London")).toBe(true);
    // 08:00Z is 08:00 local, still outside it.
    expect(inQuietHours(window, at("2026-10-26T08:00:00Z"), "Europe/London")).toBe(false);
  });

  it("follows the zone's own DST rule, not a fixed offset", () => {
    const window = { start: "09:00", end: "17:00" };
    // 20 October 2026 is still BST (UTC+1), so 10:00Z is 11:00 local: inside.
    expect(inQuietHours(window, at("2026-10-20T10:00:00Z"), "Europe/London")).toBe(true);
    // The same clock reading a week later is 10:00 local — still inside, but the
    // boundary case moves: 08:30Z is 09:30 BST (inside) on the 20th and 08:30 GMT
    // (outside) on the 26th.
    expect(inQuietHours(window, at("2026-10-20T08:30:00Z"), "Europe/London")).toBe(true);
    expect(inQuietHours(window, at("2026-10-26T08:30:00Z"), "Europe/London")).toBe(false);
  });

  it("treats the end of a window as already open", () => {
    expect(inQuietHours({ start: "22:00", end: "07:00" }, at("2026-10-26T01:00:00Z"), "Asia/Dhaka")).toBe(
      false,
    ); // exactly 07:00 local
  });

  it("never silences notifications on a malformed or disabled window", () => {
    expect(inQuietHours(null, at("2026-10-26T17:30:00Z"), "Asia/Dhaka")).toBe(false);
    expect(inQuietHours({ start: "nope", end: "07:00" }, at("2026-10-26T17:30:00Z"), "Asia/Dhaka")).toBe(
      false,
    );
    expect(
      inQuietHours(
        { start: "22:00", end: "07:00", enabled: false },
        at("2026-10-26T17:30:00Z"),
        "Asia/Dhaka",
      ),
    ).toBe(false);
  });

  it("returns null for an unknown timezone instead of throwing", () => {
    expect(localMinutes(at("2026-10-26T17:30:00Z"), "Mars/Olympus")).toBeNull();
  });

  it("computes minutes since local midnight", () => {
    // 17:30 UTC is 23:30 in Dhaka (UTC+6).
    expect(localMinutes(at("2026-10-26T17:30:00Z"), "Asia/Dhaka")).toBe(23 * 60 + 30);
  });
});
