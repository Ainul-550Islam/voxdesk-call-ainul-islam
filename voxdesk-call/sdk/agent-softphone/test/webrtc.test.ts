import { describe, expect, it, vi } from "vitest";
import { SignalingChannel, makeSignalingUrl } from "../src/signaling.js";
import { WebRTCDevice, TwilioConnectionAdapter, assertDtmf } from "../src/webrtc.js";
import type { DeviceFactory, SignalingEvent, VoiceConnection, VoiceDevice, WebSocketLike } from "../src/types.js";

describe("WebRTC adapter", () => {
  it("adapts incoming-call controls to the provider connection", () => {
    const provider = {
      on: vi.fn(), sendDigits: vi.fn(), disconnect: vi.fn(), accept: vi.fn(),
      reject: vi.fn(), mute: vi.fn(), isMuted: vi.fn(() => true),
    };
    const connection = new TwilioConnectionAdapter(provider as any);
    connection.accept();
    connection.mute(true);
    expect(provider.accept).toHaveBeenCalledOnce();
    expect(provider.mute).toHaveBeenCalledWith(true);
    expect(connection.isMuted()).toBe(true);
    connection.reject();
    expect(provider.reject).toHaveBeenCalledOnce();
  });

  it("validates DTMF and joins with the conference parameters", async () => {
    const connection: VoiceConnection = { on: vi.fn(), sendDigits: vi.fn(), disconnect: vi.fn() };
    const device: VoiceDevice = {
      on: vi.fn(), register: vi.fn(async () => undefined), updateToken: vi.fn(async () => undefined),
      connect: vi.fn(async () => connection), destroy: vi.fn(),
    };
    const factory: DeviceFactory = () => device;
    const webrtc = new WebRTCDevice("token", [{ urls: "stun:example.test" }], factory);
    await webrtc.join("call-1", "session-1");
    webrtc.sendDigits(connection, "123#");
    expect(device.connect).toHaveBeenCalledWith({ params: { call_id: "call-1", session_id: "session-1", To: "client:conference" } });
    expect(connection.sendDigits).toHaveBeenCalledWith("123#");
    expect(assertDtmf("A*9")).toBe("A*9");
    expect(() => assertDtmf("123;DROP")).toThrow();
  });
});

class FakeSocket implements WebSocketLike {
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];
  constructor() { queueMicrotask(() => { this.readyState = 1; this.onopen?.(); }); }
  send(data: string): void { this.sent.push(data); }
  close(): void { this.readyState = 3; this.onclose?.(); }
  emit(value: unknown): void { this.onmessage?.({ data: JSON.stringify(value) }); }
}

describe("SignalingChannel", () => {
  it("uses a ticketed WebSocket and parses only structured events", async () => {
    const socket = new FakeSocket();
    const events: SignalingEvent[] = [];
    const channel = new SignalingChannel(
      makeSignalingUrl("https://desk.example.test", "ticket-123"),
      { onEvent: (event) => events.push(event), onError: vi.fn() },
      () => socket,
      60_000,
    );
    await channel.connect();
    socket.emit({ id: "event-1", type: "call.updated", call_id: "call-1", payload: { call: { id: "call-1" } } });
    channel.send({ type: "ping" });
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe("call.updated");
    expect(socket.sent).toEqual([JSON.stringify({ type: "ping" })]);
    channel.close();
  });

  it("serializes SDP, ICE, attachment, and DTMF without exposing the ticket in payloads", async () => {
    const socket = new FakeSocket();
    const channel = new SignalingChannel(
      makeSignalingUrl("https://desk.example.test", "ticket-456"),
      { onEvent: vi.fn(), onError: vi.fn() },
      () => socket,
      60_000,
    );
    await channel.connect();
    channel.attach("call-1");
    channel.offer("call-1", { type: "offer", sdp: "v=0" });
    channel.answer("call-1", { type: "answer", sdp: "v=0" });
    channel.ice("call-1", { candidate: "candidate:1", sdpMLineIndex: 0 });
    channel.dtmf("call-1", "9#");
    channel.detach("call-1");
    const messages = socket.sent.map((value) => JSON.parse(value));
    expect(messages.map((value) => value.type)).toEqual([
      "call.attach", "webrtc.offer", "webrtc.answer", "webrtc.ice", "call.dtmf", "call.detach",
    ]);
    expect(messages[1].payload.description.sdp).toBe("v=0");
    expect(messages[3].payload.candidate.sdpMLineIndex).toBe(0);
    expect(socket.sent.join(" ")).not.toContain("ticket-456");
    channel.close();
  });
});
