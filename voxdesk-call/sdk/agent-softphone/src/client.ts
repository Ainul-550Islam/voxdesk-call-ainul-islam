import { SignalingChannel, makeSignalingUrl } from "./signaling.js";
import { WebRTCDevice, assertDtmf } from "./webrtc.js";
import type {
  CallResponse,
  CallsResponse,
  ClientOptions,
  DeviceFactory,
  IceServer,
  RegisterOptions,
  SessionResponse,
  SignalingChannelLike,
  SignalingEvent,
  SignalingFactory,
  SoftphoneCall,
  SoftphoneEventMap,
  SoftphoneSession,
  VoiceConnection,
} from "./types.js";

export class SoftphoneApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "http_error",
    readonly detail: unknown = undefined,
  ) {
    super(message);
    this.name = "SoftphoneApiError";
  }
}

export function requestKey(): string {
  const cryptoObject = globalThis.crypto;
  if (cryptoObject?.randomUUID) return cryptoObject.randomUUID();
  const bytes = new Uint8Array(16);
  if (cryptoObject?.getRandomValues) cryptoObject.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

type Listener = (value: never) => void;

export class SoftphoneClient {
  private readonly options: ClientOptions;
  private readonly apiRoot: string;
  private readonly listeners = new Map<string, Set<(value: unknown) => void>>();
  private readonly connections = new Map<string, VoiceConnection>();
  private readonly deviceFactory: DeviceFactory | undefined;
  private readonly signalingFactory: SignalingFactory | undefined;
  private session: SoftphoneSession | null = null;
  private device: WebRTCDevice | null = null;
  private signaling: SignalingChannelLike | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;

  constructor(options: ClientOptions) {
    if (!options.baseUrl && typeof window === "undefined" && !options.fetch) {
      throw new Error("baseUrl is required outside a browser");
    }
    this.options = options;
    const base = options.baseUrl.replace(/\/$/, "");
    this.apiRoot = base.endsWith("/api/cc2/softphone") ? base : `${base}/api/cc2/softphone`;
    this.deviceFactory = options.deviceFactory;
    this.signalingFactory = options.signalingFactory;
  }

  get currentSession(): SoftphoneSession | null { return this.session; }

  on<K extends keyof SoftphoneEventMap>(event: K, listener: (value: SoftphoneEventMap[K]) => void): () => void {
    const set = this.listeners.get(event) ?? new Set<(value: unknown) => void>();
    set.add(listener as (value: unknown) => void);
    this.listeners.set(event, set);
    return () => set.delete(listener as (value: unknown) => void);
  }

  private emit<K extends keyof SoftphoneEventMap>(event: K, value: SoftphoneEventMap[K]): void {
    for (const listener of this.listeners.get(event) ?? []) {
      try { listener(value); } catch (error) { queueMicrotask(() => { throw error; }); }
    }
  }

  private async request<T>(
    path: string,
    init: { method?: string; body?: unknown; idempotencyKey?: string } = {},
  ): Promise<T> {
    const fetcher = this.options.fetch ?? globalThis.fetch;
    if (!fetcher) throw new Error("fetch is unavailable; provide ClientOptions.fetch");
    const headers: Record<string, string> = { Accept: "application/json" };
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    const accessToken = this.options.getAccessToken ? await this.options.getAccessToken() : this.options.accessToken;
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    if (init.idempotencyKey) headers["Idempotency-Key"] = init.idempotencyKey;
    const requestInit: RequestInit = {
      method: init.method ?? "GET",
      headers,
      ...(init.body === undefined ? {} : { body: JSON.stringify(init.body) }),
    };
    const response = await fetcher(`${this.apiRoot}${path}`, requestInit);
    const text = await response.text();
    let payload: any = undefined;
    if (text) {
      try { payload = JSON.parse(text); } catch { throw new SoftphoneApiError("Softphone API returned invalid JSON", response.status); }
    }
    if (!response.ok) {
      const detail = payload?.detail;
      const code = typeof detail?.code === "string" ? detail.code : typeof payload?.code === "string" ? payload.code : "http_error";
      const message = typeof detail?.message === "string" ? detail.message : typeof payload?.message === "string" ? payload.message : `Softphone API request failed (${response.status})`;
      throw new SoftphoneApiError(message, response.status, code, detail ?? payload);
    }
    return payload as T;
  }

  private assertSession(value: SoftphoneSession): SoftphoneSession {
    if (!value?.id || !value.token || !Array.isArray(value.ice_servers)) {
      throw new Error("Softphone registration response omitted credentials or ICE servers");
    }
    return value;
  }

  async register(options: RegisterOptions): Promise<SoftphoneSession> {
    const response = await this.request<SessionResponse>("/sessions", {
      method: "POST",
      idempotencyKey: options.idempotencyKey ?? requestKey(),
      body: {
        environment_id: options.environmentId,
        device_label: options.deviceLabel,
        ...(options.deviceFingerprint ? { device_fingerprint: options.deviceFingerprint } : {}),
        device_kind: options.deviceKind ?? "browser",
      },
    });
    const session = this.assertSession(response.session);
    const nextDevice = new WebRTCDevice(session.token, session.ice_servers, this.deviceFactory);
    nextDevice.on("error", (error) => this.emit("error", error));
    nextDevice.on("incoming", (connection) => this.emit("incoming", connection as VoiceConnection));
    await nextDevice.register();
    this.device?.destroy();
    this.device = nextDevice;
    this.session = session;
    this.startHeartbeat();
    this.emit("session", session);
    if (session.websocket_ticket && (this.signalingFactory || typeof WebSocket !== "undefined")) {
      await this.connectSignaling();
    }
    return session;
  }

  private startHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    const ttl = this.session?.token_ttl_seconds ?? 300;
    const interval = this.options.heartbeatIntervalMs ?? Math.max(15_000, Math.min(60_000, ttl * 500));
    this.heartbeat = setInterval(() => { void this.heartbeatNow().catch((error) => this.emit("error", error)); }, interval);
  }

  async heartbeatNow(): Promise<SoftphoneSession> {
    if (!this.session) throw new Error("Register the softphone first");
    const response = await this.request<SessionResponse>(`/sessions/${this.session.id}/heartbeat`, { method: "POST" });
    const session = this.assertSession(response.session);
    this.session = session;
    await this.device?.updateToken(session.token);
    if (session.websocket_ticket) await this.connectSignaling();
    this.emit("session", session);
    return session;
  }

  async refreshToken(): Promise<SoftphoneSession> {
    if (!this.session) throw new Error("Register the softphone first");
    const session = this.assertSession(await this.request<SoftphoneSession>(`/sessions/${this.session.id}/token`, { method: "POST" }));
    this.session = session;
    await this.device?.updateToken(session.token);
    if (session.websocket_ticket) await this.connectSignaling();
    this.emit("session", session);
    return session;
  }

  async connectSignaling(): Promise<void> {
    if (!this.session?.websocket_ticket) return;
    this.signaling?.close();
    const ticket = this.session.websocket_ticket;
    const path = this.options.signalingUrl ?? this.session.signaling_url ?? "/api/cc2/softphone/signaling";
    const url = path.startsWith("ws://") || path.startsWith("wss://")
      ? `${path}${path.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`
      : makeSignalingUrl(this.options.baseUrl, ticket, path);
    const factory = this.signalingFactory ?? ((value: string, handlers: { onEvent: (event: SignalingEvent) => void; onError: (error: unknown) => void }) => new SignalingChannel(value, handlers));
    this.signaling = factory(url, {
      onEvent: (event) => {
        this.emit("signaling", event);
        const call = event.payload.call as SoftphoneCall | undefined;
        if (event.type === "call.updated" && call) this.emit("call", call);
      },
      onError: (error) => this.emit("error", error),
    });
    await this.signaling.connect();
    // A ticket is single-use. Do not retry the same ticket after a socket error.
    delete this.session.websocket_ticket;
  }

  async listCalls(limit = 100): Promise<SoftphoneCall[]> {
    const response = await this.request<CallsResponse>(`/calls?limit=${encodeURIComponent(limit)}`);
    return response.calls;
  }

  async getCall(callId: string): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}`);
    return response.call;
  }

  async dial(destination: string, callerIdNumber?: string, idempotencyKey = requestKey()): Promise<SoftphoneCall> {
    if (!this.session) throw new Error("Register the softphone first");
    const response = await this.request<CallResponse>("/calls", {
      method: "POST", idempotencyKey,
      body: { session_id: this.session.id, destination, ...(callerIdNumber ? { caller_id_number: callerIdNumber } : {}) },
    });
    this.emit("call", response.call);
    return response.call;
  }

  async join(call: SoftphoneCall): Promise<void> {
    if (!this.device || !this.session) throw new Error("Register the softphone first");
    if (call.session_id !== this.session.id) throw new Error("This call belongs to another registration");
    if (this.connections.has(call.id)) return;
    this.signaling?.send({ type: "call.attach", call_id: call.id });
    let connection: VoiceConnection;
    try {
      connection = await this.device.join(call.id, this.session.id);
    } catch (error) {
      try { this.signaling?.send({ type: "call.detach", call_id: call.id }); }
      catch (detachError) { this.emit("error", detachError); }
      throw error;
    }
    connection.on("error", (error) => this.emit("error", error));
    connection.on("disconnect", () => this.connections.delete(call.id));
    this.connections.set(call.id, connection);
  }

  attachCall(callId: string): void {
    if (!this.signaling) throw new Error("Connect signaling before attaching a call");
    this.signaling.send({ type: "call.attach", call_id: callId });
  }

  detachCall(callId: string): void {
    this.signaling?.send({ type: "call.detach", call_id: callId });
  }

  sendOffer(callId: string, description: RTCSessionDescriptionInit): void {
    if (!this.signaling) throw new Error("Connect signaling before sending an offer");
    this.signaling.send({ type: "webrtc.offer", call_id: callId, payload: { description } });
  }

  sendAnswer(callId: string, description: RTCSessionDescriptionInit): void {
    if (!this.signaling) throw new Error("Connect signaling before sending an answer");
    this.signaling.send({ type: "webrtc.answer", call_id: callId, payload: { description } });
  }

  sendIceCandidate(callId: string, candidate: RTCIceCandidateInit): void {
    if (!this.signaling) throw new Error("Connect signaling before sending ICE");
    this.signaling.send({ type: "webrtc.ice", call_id: callId, payload: { candidate } });
  }

  async control(callId: string, action: "mute" | "hold", enabled: boolean): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/${action}`, { method: "POST", body: { enabled } });
    this.emit("call", response.call);
    return response.call;
  }

  async resume(callId: string): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/resume`, { method: "POST" });
    this.emit("call", response.call);
    return response.call;
  }

  async setRecordingPaused(callId: string, paused: boolean): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/record-pause`, {
      method: "POST", body: { enabled: paused },
    });
    this.emit("call", response.call);
    return response.call;
  }

  async addToConference(callId: string, destination: string): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/conference`, {
      method: "POST", body: { destination, mode: "warm" },
    });
    this.emit("call", response.call);
    return response.call;
  }

  sendDigits(callId: string, digits: string): void {
    const connection = this.connections.get(callId);
    if (!connection) throw new Error("Join the browser leg before sending DTMF");
    connection.sendDigits(assertDtmf(digits));
  }

  async transfer(callId: string, destination: string, mode: "blind" | "warm"): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/transfer`, { method: "POST", body: { destination, mode } });
    this.emit("call", response.call);
    return response.call;
  }

  async finishTransfer(callId: string, action: "complete" | "cancel"): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/transfer/${action}`, { method: "POST" });
    this.emit("call", response.call);
    return response.call;
  }

  async end(callId: string): Promise<SoftphoneCall> {
    const response = await this.request<CallResponse>(`/calls/${encodeURIComponent(callId)}/end`, { method: "POST" });
    this.connections.get(callId)?.disconnect();
    this.connections.delete(callId);
    try { this.signaling?.send({ type: "call.detach", call_id: callId }); }
    catch (error) { this.emit("error", error); }
    this.emit("call", response.call);
    return response.call;
  }

  async revoke(): Promise<void> {
    if (!this.session) return;
    await this.request(`/sessions/${encodeURIComponent(this.session.id)}`, { method: "DELETE" });
    this.destroy();
  }

  destroy(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    this.signaling?.close();
    this.signaling = null;
    for (const connection of this.connections.values()) connection.disconnect();
    this.connections.clear();
    this.device?.destroy();
    this.device = null;
    this.session = null;
  }
}

export type { IceServer };
