import { Device } from "@twilio/voice-sdk";
import type {
  DeviceFactory,
  IceServer,
  VoiceConnection,
  VoiceDevice,
} from "./types.js";

class TwilioConnectionAdapter implements VoiceConnection {
  constructor(private readonly connection: import("@twilio/voice-sdk").Call) {}

  on(event: "accept" | "disconnect" | "error", listener: (value?: unknown) => void): unknown {
    return this.connection.on(event, listener);
  }

  sendDigits(digits: string): void {
    this.connection.sendDigits(digits);
  }

  disconnect(): void {
    this.connection.disconnect();
  }

  accept(): void { this.connection.accept(); }
  reject(): void { this.connection.reject(); }
  mute(muted: boolean): void { this.connection.mute(muted); }
  isMuted(): boolean { return this.connection.isMuted(); }
}

class TwilioDeviceAdapter implements VoiceDevice {
  private readonly device: import("@twilio/voice-sdk").Device;

  constructor(token: string, private readonly iceServers: IceServer[]) {
    this.device = new Device(token);
  }

  on(event: "registered" | "unregistered" | "incoming" | "error", listener: (value?: unknown) => void): unknown {
    if (event === "incoming") {
      return this.device.on(event, (call: import("@twilio/voice-sdk").Call) => {
        listener(new TwilioConnectionAdapter(call));
      });
    }
    return this.device.on(event, listener);
  }

  async register(): Promise<void> {
    await this.device.register();
  }

  async updateToken(token: string): Promise<void> {
    await this.device.updateToken(token);
  }

  async connect(options: { params: Record<string, string> }): Promise<VoiceConnection> {
    const connection = await this.device.connect({
      ...options,
      rtcConfiguration: { iceServers: this.iceServers },
    });
    return new TwilioConnectionAdapter(connection);
  }

  destroy(): void {
    this.device.destroy();
  }
}

export const defaultDeviceFactory: DeviceFactory = (token, iceServers) =>
  new TwilioDeviceAdapter(token, iceServers);

export function assertDtmf(digits: string): string {
  if (!/^[0-9*#A-Da-d]{1,32}$/.test(digits)) {
    throw new Error("DTMF must contain 1–32 digits, *, #, or A–D characters");
  }
  return digits;
}

/** A small provider-neutral facade; all carrier state still comes from Twilio. */
export class WebRTCDevice implements VoiceDevice {
  private readonly device: VoiceDevice;

  constructor(token: string, iceServers: IceServer[], factory: DeviceFactory = defaultDeviceFactory) {
    this.device = factory(token, iceServers);
  }

  on(event: "registered" | "unregistered" | "incoming" | "error", listener: (value?: unknown) => void): unknown {
    return this.device.on(event, listener);
  }

  register(): Promise<void> {
    return this.device.register();
  }

  updateToken(token: string): Promise<void> {
    if (!token) return Promise.reject(new Error("A non-empty Twilio access token is required"));
    return this.device.updateToken(token);
  }

  connect(options: { params: Record<string, string> }): Promise<VoiceConnection> {
    return this.device.connect(options);
  }

  async join(callId: string, sessionId: string): Promise<VoiceConnection> {
    if (!callId || !sessionId) throw new Error("A call and registration are required to join");
    return this.connect({ params: { call_id: callId, session_id: sessionId, To: "client:conference" } });
  }

  sendDigits(connection: VoiceConnection, digits: string): void {
    connection.sendDigits(assertDtmf(digits));
  }

  destroy(): void {
    this.device.destroy();
  }
}

export { WebRTCDevice as WebRtcDevice, TwilioConnectionAdapter, TwilioDeviceAdapter };
