import { describe, expect, it, vi } from "vitest";
import { SoftphoneClient } from "../src/client.js";
import type { DeviceFactory, SignalingFactory, SoftphoneSession, VoiceConnection, VoiceDevice } from "../src/types.js";

const session: SoftphoneSession = {
  id: "session-1",
  user_id: "user-1",
  environment_id: "env-1",
  client_identity: "agent-user-1",
  device_label: "test browser",
  status: "active",
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  token: "twilio-token",
  token_ttl_seconds: 300,
  ice_servers: [{ urls: "stun:stun.example.test" }],
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function fakeDevice(): { factory: DeviceFactory; connection: VoiceConnection; device: VoiceDevice } {
  const connection: VoiceConnection = {
    on: vi.fn(),
    sendDigits: vi.fn(),
    disconnect: vi.fn(),
  };
  const device: VoiceDevice = {
    on: vi.fn(),
    register: vi.fn(async () => undefined),
    updateToken: vi.fn(async () => undefined),
    connect: vi.fn(async () => connection),
    destroy: vi.fn(),
  };
  return { factory: () => device, connection, device };
}

describe("SoftphoneClient", () => {
  it("registers, dials with idempotency, joins and controls a real adapter boundary", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.endsWith("/sessions")) return response({ session });
      if (url.endsWith("/calls")) return response({ call: { id: "call-1", session_id: session.id, agent_user_id: "user-1", origin: "agent_outbound", status: "dialing", conference_name: "conf", muted: false, on_hold: false } });
      return response({ call: { id: "call-1", session_id: session.id, agent_user_id: "user-1", origin: "agent_outbound", status: "completed", conference_name: "conf", muted: false, on_hold: false } });
    }) as unknown as typeof fetch;
    const fake = fakeDevice();
    const signaling: SignalingFactory = (_url, handlers) => ({
      connect: async () => { handlers.onEvent({ id: "event-1", type: "session.heartbeat", payload: {} }); },
      send: vi.fn(),
      close: vi.fn(),
    });
    const client = new SoftphoneClient({
      baseUrl: "https://desk.example.test",
      fetch: fetcher,
      deviceFactory: fake.factory,
      signalingFactory: signaling,
      heartbeatIntervalMs: 60_000,
    });
    await client.register({ environmentId: "env-1", deviceLabel: "browser", idempotencyKey: "register-1" });
    const call = await client.dial("+15550001111", "+15550002222", "dial-1");
    await client.join(call);
    client.sendDigits(call.id, "12#");
    await client.control(call.id, "mute", true);
    await client.end(call.id);
    expect(calls[0]?.url).toBe("https://desk.example.test/api/cc2/softphone/sessions");
    expect(calls[0]?.init?.headers).toMatchObject({ "Idempotency-Key": "register-1" });
    expect(calls[1]?.init?.headers).toMatchObject({ "Idempotency-Key": "dial-1" });
    expect(fake.connection.sendDigits).toHaveBeenCalledWith("12#");
    expect(fake.device.register).toHaveBeenCalledOnce();
    client.destroy();
  });

  it("does not connect signaling for a ticketless heartbeat", async () => {
    const fetcher = vi.fn(async () => response({ session })) as unknown as typeof fetch;
    const signaling = vi.fn() as unknown as SignalingFactory;
    const fake = fakeDevice();
    const client = new SoftphoneClient({
      baseUrl: "https://desk.example.test", fetch: fetcher,
      deviceFactory: fake.factory, signalingFactory: signaling, heartbeatIntervalMs: 60_000,
    });
    await client.register({ environmentId: "env-1", deviceLabel: "browser", idempotencyKey: "register-no-ticket" });
    await client.heartbeatNow();
    expect(signaling).not.toHaveBeenCalled();
    client.destroy();
  });

  it("uses the dedicated resume, recording and conference endpoints", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return response({ call: { id: "call-1", agent_user_id: "user-1", origin: "acd", status: "in_progress", conference_name: "conf", muted: false, on_hold: false, recording_paused: true } });
    }) as unknown as typeof fetch;
    const client = new SoftphoneClient({ baseUrl: "https://desk.example.test", fetch: fetcher });
    await client.resume("call-1");
    await client.setRecordingPaused("call-1", true);
    await client.addToConference("call-1", "+15550003000");
    expect(calls.map((entry) => entry.url)).toEqual([
      "https://desk.example.test/api/cc2/softphone/calls/call-1/resume",
      "https://desk.example.test/api/cc2/softphone/calls/call-1/record-pause",
      "https://desk.example.test/api/cc2/softphone/calls/call-1/conference",
    ]);
    expect(calls[1]?.init?.body).toBe(JSON.stringify({ enabled: true }));
    expect(calls[2]?.init?.body).toBe(JSON.stringify({ destination: "+15550003000", mode: "warm" }));
  });

  it("surfaces structured refusals instead of converting them to success", async () => {
    const fetcher = vi.fn(async () => response({ detail: { code: "consent_required", message: "Consent is required" } }, 409)) as unknown as typeof fetch;
    const client = new SoftphoneClient({ baseUrl: "https://desk.example.test", fetch: fetcher });
    await expect(client.listCalls()).rejects.toMatchObject({ status: 409, code: "consent_required" });
  });
});
