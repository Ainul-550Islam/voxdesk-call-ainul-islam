import { api, requestKey } from "./lib/api";
import type { TwilioConnection, TwilioDevice } from "./twilio";
import type { SoftphoneCall, SoftphoneSession } from "./types";

type SessionResponse = { session: SoftphoneSession };
type CallResponse = { call: SoftphoneCall; replayed?: boolean };
type CallsResponse = { calls: SoftphoneCall[] };

export class BrowserSoftphone {
  private device: TwilioDevice | null = null;
  private session: SoftphoneSession | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private readonly connections = new Map<string, TwilioConnection>();

  constructor(private readonly onError: (error: unknown) => void) {}

  get currentSession(): SoftphoneSession | null {
    return this.session;
  }

  async register(environmentId: string, deviceLabel: string): Promise<SoftphoneSession> {
    if (!window.Twilio?.Device) {
      throw new Error("The Twilio Voice SDK has not loaded");
    }
    const response = await api<SessionResponse>("/api/cc2/softphone/sessions", {
      method: "POST",
      body: { environment_id: environmentId, device_label: deviceLabel },
      idempotencyKey: requestKey(),
    });
    this.device?.destroy();
    this.device = new window.Twilio.Device(response.session.token);
    this.device.on("error", this.onError);
    await this.device.register();
    this.session = response.session;
    this.startHeartbeat();
    return response.session;
  }

  private startHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    const ttl = this.session?.token_ttl_seconds ?? 60;
    this.heartbeat = setInterval(() => {
      void this.refresh().catch(this.onError);
    }, Math.max(15_000, Math.min(60_000, ttl * 500)));
  }

  async refresh(): Promise<void> {
    if (!this.session) return;
    const response = await api<SessionResponse>(
      `/api/cc2/softphone/sessions/${this.session.id}/heartbeat`,
      { method: "POST" },
    );
    this.session = response.session;
    await this.device?.updateToken(response.session.token);
  }

  async listCalls(): Promise<SoftphoneCall[]> {
    const response = await api<CallsResponse>("/api/cc2/softphone/calls?limit=100");
    return response.calls;
  }

  async dial(destination: string, callerIdNumber: string): Promise<SoftphoneCall> {
    if (!this.session) throw new Error("Register the softphone first");
    const response = await api<CallResponse>("/api/cc2/softphone/calls", {
      method: "POST",
      body: {
        session_id: this.session.id,
        destination,
        ...(callerIdNumber ? { caller_id_number: callerIdNumber } : {}),
      },
      idempotencyKey: requestKey(),
    });
    return response.call;
  }

  async join(call: SoftphoneCall): Promise<void> {
    if (!this.device || !this.session) throw new Error("Register the softphone first");
    if (call.session_id !== this.session.id) throw new Error("This call belongs to another registration");
    if (this.connections.has(call.id)) return;
    const connection = await this.device.connect({
      params: { call_id: call.id, session_id: this.session.id, To: "client:conference" },
      rtcConfiguration: { iceServers: this.session.ice_servers },
    });
    connection.on("error", this.onError);
    connection.on("disconnect", () => this.connections.delete(call.id));
    this.connections.set(call.id, connection);
  }

  async control(callId: string, action: "mute" | "hold", enabled: boolean): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/${action}`, {
      method: "POST",
      body: { enabled },
    });
    return response.call;
  }

  async resume(callId: string): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/resume`, { method: "POST" });
    return response.call;
  }

  async recordPause(callId: string, paused: boolean): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/record-pause`, {
      method: "POST", body: { enabled: paused },
    });
    return response.call;
  }

  async conference(callId: string, destination: string): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/conference`, {
      method: "POST", body: { destination, mode: "warm" },
    });
    return response.call;
  }

  sendDigits(callId: string, digits: string): void {
    const connection = this.connections.get(callId);
    if (!connection) throw new Error("Join the browser leg before sending DTMF");
    connection.sendDigits(digits);
  }

  async transfer(callId: string, destination: string, mode: "blind" | "warm"): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/transfer`, {
      method: "POST",
      body: { destination, mode },
    });
    return response.call;
  }

  async finishTransfer(callId: string, action: "complete" | "cancel"): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/transfer/${action}`, {
      method: "POST",
    });
    return response.call;
  }

  async end(callId: string): Promise<SoftphoneCall> {
    const response = await api<CallResponse>(`/api/cc2/softphone/calls/${callId}/end`, {
      method: "POST",
    });
    this.connections.get(callId)?.disconnect();
    this.connections.delete(callId);
    return response.call;
  }

  destroy(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    this.device?.destroy();
    this.device = null;
    this.session = null;
    this.connections.clear();
  }
}
