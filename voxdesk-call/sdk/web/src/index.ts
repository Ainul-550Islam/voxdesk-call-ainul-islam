/**
 * `@voxdesk/web-sdk` — Browser Voice Client SDK (`sdk/web/src/index.ts`).
 *
 * Exposes `VoxDeskWebClient`:
 * - `startCall({ accessToken, url, transport, sampleRate, constraints })`
 * - `stopCall()`
 * - `mute()` / `unmute()`
 * - `sendDtmf(digit)`
 * - `on("call_started" | "call_ended" | "agent_start_talking" | "agent_stop_talking" | "transcript" | "error" | "latency", handler)`
 */

import {
  AudioCaptureConstraints,
  buildMediaTrackConstraints,
  downsampleFloat32,
  float32ToPcm16Bytes,
  JitterSafePlaybackQueue,
  pcm16BytesToFloat32,
  TARGET_CHANNELS,
  TARGET_SAMPLE_RATE,
  WorkletAudioCapture,
} from "./audio/worklet-capture";
import {
  decodePipecatFrame,
  encodePipecatFrame,
  PIPECAT_FRAMES_DESCRIPTOR,
  PipecatFrame,
  WsProtobufTransport,
} from "./transport/ws-protobuf";
import { SmallWebRTCClient } from "./transport/webrtc";

export {
  buildMediaTrackConstraints,
  decodePipecatFrame,
  downsampleFloat32,
  encodePipecatFrame,
  float32ToPcm16Bytes,
  JitterSafePlaybackQueue,
  pcm16BytesToFloat32,
  PIPECAT_FRAMES_DESCRIPTOR,
  SmallWebRTCClient,
  TARGET_CHANNELS,
  TARGET_SAMPLE_RATE,
  WorkletAudioCapture,
  WsProtobufTransport,
};
export type { AudioCaptureConstraints, PipecatFrame };

export type VoxDeskEventName =
  | "call_started"
  | "call_ended"
  | "agent_start_talking"
  | "agent_stop_talking"
  | "transcript"
  | "error"
  | "latency";

export interface TranscriptEventPayload {
  role: "user" | "assistant";
  text: string;
  final?: boolean;
  timestamp?: string;
}

export interface LatencyEventPayload {
  totalMs: number;
  sttMs?: number;
  llmMs?: number;
  ttsMs?: number;
}

export interface VoxDeskEventMap {
  call_started: { callId?: string; transport: "ws-protobuf" | "small-webrtc" };
  call_ended: { code?: number; reason?: string };
  agent_start_talking: { timestamp: number };
  agent_stop_talking: { timestamp: number };
  transcript: TranscriptEventPayload;
  error: Error;
  latency: LatencyEventPayload;
}

export interface StartCallOptions {
  accessToken: string;
  url?: string;
  transport?: "ws-protobuf" | "small-webrtc";
  sampleRate?: number;
  constraints?: AudioCaptureConstraints;
}

type EventHandler<K extends VoxDeskEventName> = (
  payload: VoxDeskEventMap[K]
) => void;

const VALID_DTMF_DIGITS = /^[0-9*#A-D]$/i;

export class VoxDeskWebClient {
  private readonly listeners: Record<
    string,
    Set<(payload: unknown) => void>
  > = {};

  private wsTransport: WsProtobufTransport | null = null;
  private webrtcClient: SmallWebRTCClient | null = null;
  private audioCapture: WorkletAudioCapture | null = null;
  private playbackQueue: JitterSafePlaybackQueue;
  private active = false;
  private muted = false;
  private agentTalking = false;
  private sampleRate = TARGET_SAMPLE_RATE;

  constructor() {
    this.playbackQueue = new JitterSafePlaybackQueue(TARGET_SAMPLE_RATE);
  }

  public on<K extends VoxDeskEventName>(
    event: K,
    handler: EventHandler<K>
  ): this {
    if (!this.listeners[event]) {
      this.listeners[event] = new Set();
    }
    this.listeners[event].add(handler as (payload: unknown) => void);
    return this;
  }

  public off<K extends VoxDeskEventName>(
    event: K,
    handler: EventHandler<K>
  ): this {
    this.listeners[event]?.delete(handler as (payload: unknown) => void);
    return this;
  }

  private emit<K extends VoxDeskEventName>(
    event: K,
    payload: VoxDeskEventMap[K]
  ): void {
    const handlers = this.listeners[event];
    if (!handlers) return;
    for (const handler of handlers) {
      try {
        (handler as EventHandler<K>)(payload);
      } catch {
        // isolate listener exceptions
      }
    }
  }

  private resolveWsUrl(accessToken: string, explicitUrl?: string): string {
    const base = explicitUrl || "/telephony/web/ws";
    let resolved = base;
    if (resolved.startsWith("/")) {
      const loc =
        typeof window !== "undefined" && window.location
          ? window.location
          : { protocol: "http:", host: "localhost:8000" };
      const proto = loc.protocol === "https:" ? "wss:" : "ws:";
      resolved = `${proto}//${loc.host}${resolved}`;
    } else if (resolved.startsWith("http://")) {
      resolved = "ws://" + resolved.slice("http://".length);
    } else if (resolved.startsWith("https://")) {
      resolved = "wss://" + resolved.slice("https://".length);
    }
    const separator = resolved.includes("?") ? "&" : "?";
    if (resolved.includes("token=")) {
      return resolved;
    }
    return `${resolved}${separator}token=${encodeURIComponent(accessToken)}`;
  }

  public async startCall(options: StartCallOptions): Promise<void> {
    if (!options?.accessToken || !options.accessToken.trim()) {
      const err = new Error("accessToken is required to start a web call");
      this.emit("error", err);
      throw err;
    }

    if (this.active) {
      await this.stopCall();
    }

    const transportKind = options.transport ?? "ws-protobuf";
    this.sampleRate = options.sampleRate ?? TARGET_SAMPLE_RATE;
    this.playbackQueue = new JitterSafePlaybackQueue(this.sampleRate);
    this.audioCapture = new WorkletAudioCapture();
    this.muted = false;

    try {
      await this.audioCapture.start((pcm16Bytes) => {
        if (!this.muted && this.wsTransport) {
          this.wsTransport.sendAudio(pcm16Bytes, this.sampleRate, TARGET_CHANNELS);
        }
      }, options.constraints);

      if (transportKind === "small-webrtc") {
        this.webrtcClient = new SmallWebRTCClient({
          accessToken: options.accessToken,
          offerUrl: options.url ?? "/telephony/web/offer",
          onDataMessage: (msg) => this.handleServerMessage(msg),
          onDisconnected: () => {
            if (this.active) {
              this.active = false;
              this.emit("call_ended", { code: 1000, reason: "webrtc_closed" });
            }
          },
        });
        const { callId } = await this.webrtcClient.connect();
        this.active = true;
        this.emit("call_started", { callId, transport: "small-webrtc" });
        return;
      }

      const wsUrl = this.resolveWsUrl(options.accessToken, options.url);
      this.wsTransport = new WsProtobufTransport(wsUrl, {
        onFrame: (frame) => this.handleIncomingFrame(frame),
        onError: (err) => this.emit("error", err),
        onClose: (code, reason) => {
          const wasActive = this.active;
          this.active = false;
          if (this.agentTalking) {
            this.agentTalking = false;
            this.emit("agent_stop_talking", { timestamp: Date.now() });
          }
          if (wasActive) {
            this.emit("call_ended", { code, reason });
          }
        },
      });

      await this.wsTransport.connect();
      this.active = true;
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      await this.cleanupResources();
      this.emit("error", error);
      throw error;
    }
  }

  private handleIncomingFrame(frame: PipecatFrame): void {
    if (frame.oneofKind === "audio") {
      if (!this.agentTalking) {
        this.agentTalking = true;
        this.emit("agent_start_talking", { timestamp: Date.now() });
      }
      this.playbackQueue.enqueuePcm16(
        frame.audio.audio,
        this.audioCapture?.getAudioContext() ?? null
      );
      return;
    }

    if (frame.oneofKind === "transcription") {
      this.emit("transcript", {
        role: frame.transcription.userId === "assistant" ? "assistant" : "user",
        text: frame.transcription.text,
        final: true,
        timestamp: frame.transcription.timestamp,
      });
      return;
    }

    if (frame.oneofKind === "text") {
      this.emit("transcript", {
        role: "assistant",
        text: frame.text.text,
        final: true,
      });
      return;
    }

    if (frame.oneofKind === "message") {
      try {
        const parsed = JSON.parse(frame.message.data) as Record<string, unknown>;
        this.handleServerMessage(parsed);
      } catch {
        // ignore non-JSON message payload
      }
    }
  }

  private handleServerMessage(msg: Record<string, unknown>): void {
    const type = String(msg.type ?? "");
    if (type === "call_started") {
      this.emit("call_started", {
        callId: typeof msg.call_id === "string" ? msg.call_id : undefined,
        transport: "ws-protobuf",
      });
      return;
    }
    if (type === "agent_start_talking") {
      this.agentTalking = true;
      this.emit("agent_start_talking", { timestamp: Date.now() });
      return;
    }
    if (type === "agent_stop_talking") {
      this.agentTalking = false;
      this.emit("agent_stop_talking", { timestamp: Date.now() });
      return;
    }
    if (type === "transcript") {
      this.emit("transcript", {
        role: msg.role === "user" ? "user" : "assistant",
        text: String(msg.text ?? ""),
        final: msg.final !== undefined ? Boolean(msg.final) : true,
      });
      return;
    }
    if (type === "latency") {
      this.emit("latency", {
        totalMs: Number(msg.total_ms ?? msg.totalMs ?? 0),
        sttMs: msg.stt_ms !== undefined ? Number(msg.stt_ms) : undefined,
        llmMs: msg.llm_ms !== undefined ? Number(msg.llm_ms) : undefined,
        ttsMs: msg.tts_ms !== undefined ? Number(msg.tts_ms) : undefined,
      });
      return;
    }
    if (type === "error") {
      this.emit("error", new Error(String(msg.message ?? "Web call server error")));
    }
  }

  public sendAudioPcm16(pcm16Bytes: Uint8Array): void {
    if (!this.active || this.muted || !this.wsTransport) return;
    this.wsTransport.sendAudio(pcm16Bytes, this.sampleRate, TARGET_CHANNELS);
  }

  public sendDtmf(digit: string): void {
    const trimmed = String(digit ?? "").trim().toUpperCase();
    if (!VALID_DTMF_DIGITS.test(trimmed)) {
      throw new Error(`Invalid DTMF digit: ${digit}`);
    }
    const payload = { type: "dtmf", digit: trimmed };
    if (this.wsTransport) {
      this.wsTransport.sendMessage(payload);
    } else if (this.webrtcClient) {
      this.webrtcClient.sendDataMessage(payload);
    }
  }

  public mute(): void {
    this.muted = true;
    this.audioCapture?.setMuted(true);
    if (this.wsTransport) {
      this.wsTransport.sendMessage({ type: "mute", muted: true });
    }
  }

  public unmute(): void {
    this.muted = false;
    this.audioCapture?.setMuted(false);
    if (this.wsTransport) {
      this.wsTransport.sendMessage({ type: "mute", muted: false });
    }
  }

  public isMuted(): boolean {
    return this.muted;
  }

  public isActive(): boolean {
    return this.active;
  }

  private async cleanupResources(): Promise<void> {
    if (this.wsTransport) {
      this.wsTransport.close(1000, "client_stop");
      this.wsTransport = null;
    }
    if (this.webrtcClient) {
      this.webrtcClient.close();
      this.webrtcClient = null;
    }
    if (this.audioCapture) {
      await this.audioCapture.stop();
      this.audioCapture = null;
    }
    this.playbackQueue.clear();
  }

  public async stopCall(): Promise<void> {
    const wasActive = this.active;
    this.active = false;
    if (this.wsTransport) {
      this.wsTransport.sendMessage({ type: "end_call" });
    }
    await this.cleanupResources();
    if (this.agentTalking) {
      this.agentTalking = false;
      this.emit("agent_stop_talking", { timestamp: Date.now() });
    }
    if (wasActive) {
      this.emit("call_ended", { code: 1000, reason: "stopped_by_user" });
    }
  }
}
