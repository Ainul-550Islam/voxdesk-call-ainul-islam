export type IceServer = {
  urls: string | string[];
  username?: string;
  credential?: string;
};

export type SoftphoneSession = {
  id: string;
  user_id: string;
  environment_id: string;
  device_id?: string | null;
  client_identity: string;
  device_label: string;
  status: "active" | "expired" | "revoked";
  expires_at: string;
  token: string;
  token_ttl_seconds: number;
  identity?: string;
  ice_servers: IceServer[];
  websocket_ticket?: string;
  signaling_url?: string;
};

export type SoftphoneLeg = {
  id: string;
  kind: "customer" | "agent" | "consult";
  status: string;
  provider_call_id?: string | null;
  destination_redacted?: string;
};

export type WarmTransferBriefing = {
  text_redacted: string;
  status: string;
  human_briefed: boolean;
  ai_detached: boolean;
  returned_to_ai: boolean;
};

export type CustomerContext = {
  lead: { id: string; name: string; company: string | null; score: number | null; status: string } | null;
  prior_calls: Array<{ id: string; status: string; started_at: string; duration_seconds: number; summary_redacted: string }>;
  crm: Record<string, string | number | boolean>;
  crm_synced?: boolean;
};

export type SoftphoneCall = {
  id: string;
  call_id?: string | null;
  session_id?: string | null;
  agent_user_id: string;
  origin: string;
  status: string;
  conference_name: string;
  muted: boolean;
  on_hold: boolean;
  answered_at?: string | null;
  ended_at?: string | null;
  failure_category?: string;
  recording_paused?: boolean | null;
  warm_transfer_briefing?: WarmTransferBriefing | null;
  customer_context?: CustomerContext;
  agent_joined_at?: string | null;
  legs?: SoftphoneLeg[];
};

export type SessionResponse = { session: SoftphoneSession };
export type CallResponse = { call: SoftphoneCall; replayed?: boolean; consult_leg?: SoftphoneLeg | null };
export type CallsResponse = { calls: SoftphoneCall[] };

export type RegisterOptions = {
  environmentId: string;
  deviceLabel: string;
  deviceFingerprint?: string;
  deviceKind?: "browser" | "desk_phone";
  idempotencyKey?: string;
};

export type ClientOptions = {
  baseUrl: string;
  accessToken?: string;
  getAccessToken?: () => string | Promise<string>;
  fetch?: typeof fetch;
  deviceFactory?: DeviceFactory;
  signalingFactory?: SignalingFactory;
  signalingUrl?: string;
  heartbeatIntervalMs?: number;
};

export type VoiceConnection = {
  on(event: "accept" | "disconnect" | "error", listener: (value?: unknown) => void): unknown;
  sendDigits(digits: string): void;
  disconnect(): void;
  accept?(): void;
  reject?(): void;
  mute?(muted: boolean): void;
  isMuted?(): boolean;
};

export type VoiceDevice = {
  on(event: "registered" | "unregistered" | "incoming" | "error", listener: (value?: unknown) => void): unknown;
  register(): Promise<void>;
  updateToken(token: string): Promise<void>;
  connect(options: { params: Record<string, string> }): Promise<VoiceConnection>;
  destroy(): void;
};

export type DeviceFactory = (token: string, iceServers: IceServer[]) => VoiceDevice;

export type SignalingEvent = {
  id: string;
  type: "call.updated" | "call.attached" | "call.detached" | "session.heartbeat" | "assist.updated"
    | "webrtc.offer" | "webrtc.answer" | "webrtc.ice" | "call.dtmf" | "signaling.ack" | "pong" | "error";
  call_id?: string | null;
  payload: Record<string, any>;
};

export type ClientSignalingMessage = {
  type: "ping" | "heartbeat" | "call.attach" | "call.detach" | "call.hold" | "call.resume"
    | "call.mute" | "call.dtmf" | "webrtc.offer" | "webrtc.answer" | "webrtc.ice";
  call_id?: string;
  payload?: Record<string, unknown>;
};

export type WebSocketLike = {
  readonly readyState: number;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: (() => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
};

export type SignalingFactory = (
  url: string,
  handlers: { onEvent: (event: SignalingEvent) => void; onError: (error: unknown) => void },
) => SignalingChannelLike;

export type SignalingChannelLike = {
  connect(): Promise<void>;
  send(event: ClientSignalingMessage): void;
  close(): void;
};

export type SoftphoneEventMap = {
  session: SoftphoneSession;
  call: SoftphoneCall;
  signaling: SignalingEvent;
  incoming: VoiceConnection;
  error: unknown;
};
