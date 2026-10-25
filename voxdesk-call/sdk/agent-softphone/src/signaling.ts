import type {
  ClientSignalingMessage,
  SignalingChannelLike,
  SignalingEvent,
  SignalingFactory,
  WebSocketLike,
} from "./types.js";

export function makeSignalingUrl(baseUrl: string, ticket: string, path = "/api/cc2/softphone/signaling"): string {
  if (!ticket) throw new Error("A one-time signaling ticket is required");
  const origin = baseUrl || (typeof window !== "undefined" ? window.location.origin : "");
  if (!origin) throw new Error("An absolute softphone base URL is required outside a browser");
  const url = new URL(path, origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  return url.toString();
}

function isEvent(value: unknown): value is SignalingEvent {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<SignalingEvent>;
  return typeof item.id === "string" && typeof item.type === "string" && !!item.payload && typeof item.payload === "object";
}

export class SignalingChannel implements SignalingChannelLike {
  private socket: WebSocketLike | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private closed = false;

  constructor(
    private readonly url: string,
    private readonly handlers: { onEvent: (event: SignalingEvent) => void; onError: (error: unknown) => void },
    private readonly socketFactory: (url: string) => WebSocketLike = (value) => {
      if (typeof WebSocket === "undefined") throw new Error("WebSocket is unavailable in this runtime");
      return new WebSocket(value) as unknown as WebSocketLike;
    },
    private readonly heartbeatMs = 20_000,
  ) {}

  connect(): Promise<void> {
    if (this.socket) return Promise.reject(new Error("Signaling channel is already connected"));
    this.closed = false;
    return new Promise<void>((resolve, reject) => {
      let opened = false;
      try {
        const socket = this.socketFactory(this.url);
        this.socket = socket;
        socket.onopen = () => {
          opened = true;
          this.heartbeat = setInterval(() => {
            try { this.send({ type: "heartbeat" }); } catch (error) { this.handlers.onError(error); }
          }, this.heartbeatMs);
          resolve();
        };
        socket.onmessage = ({ data }) => {
          try {
            const parsed: unknown = JSON.parse(data);
            if (!isEvent(parsed)) throw new Error("Invalid softphone signaling event");
            this.handlers.onEvent(parsed);
          } catch (error) {
            this.handlers.onError(error);
          }
        };
        socket.onerror = (error) => {
          if (!opened) reject(error);
          this.handlers.onError(error);
        };
        socket.onclose = () => {
          if (this.heartbeat) clearInterval(this.heartbeat);
          this.heartbeat = null;
          if (!opened) reject(new Error("Signaling channel closed before opening"));
          if (!this.closed) this.handlers.onError(new Error("Signaling channel closed"));
        };
      } catch (error) {
        this.socket = null;
        reject(error);
      }
    });
  }

  send(event: ClientSignalingMessage): void {
    if (!this.socket || this.socket.readyState !== 1) throw new Error("Signaling channel is not open");
    this.socket.send(JSON.stringify(event));
  }

  attach(callId: string): void { this.send({ type: "call.attach", call_id: callId }); }
  detach(callId: string): void { this.send({ type: "call.detach", call_id: callId }); }
  offer(callId: string, description: RTCSessionDescriptionInit): void {
    this.send({ type: "webrtc.offer", call_id: callId, payload: { description } });
  }
  answer(callId: string, description: RTCSessionDescriptionInit): void {
    this.send({ type: "webrtc.answer", call_id: callId, payload: { description } });
  }
  ice(callId: string, candidate: RTCIceCandidateInit): void {
    this.send({ type: "webrtc.ice", call_id: callId, payload: { candidate } });
  }
  dtmf(callId: string, digits: string): void {
    this.send({ type: "call.dtmf", call_id: callId, payload: { digits } });
  }

  close(): void {
    this.closed = true;
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    this.socket?.close(1000, "client close");
    this.socket = null;
  }
}

export const defaultSignalingFactory: SignalingFactory = (url, handlers) =>
  new SignalingChannel(url, handlers);
