/**
 * Vitest unit tests for `@voxdesk/web-sdk` (`sdk/web/tests/client.test.ts`):
 * - Protobuf frame encoding/decoding against Pipecat `frames.proto`
 * - Audio capture constraints, PCM16 conversion, and jitter playback queue
 * - `VoxDeskWebClient` connect -> send audio -> receive transcript & audio -> mute/unmute -> DTMF -> disconnect
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildMediaTrackConstraints,
  decodePipecatFrame,
  encodePipecatFrame,
  float32ToPcm16Bytes,
  pcm16BytesToFloat32,
  PIPECAT_FRAMES_DESCRIPTOR,
  TranscriptEventPayload,
  VoxDeskWebClient,
} from "../src/index";

class FakeWebSocket {
  public static CONNECTING = 0;
  public static OPEN = 1;
  public static CLOSING = 2;
  public static CLOSED = 3;
  public static instances: FakeWebSocket[] = [];

  public readonly url: string;
  public readyState = FakeWebSocket.CONNECTING;
  public binaryType = "blob";
  public sentFrames: Uint8Array[] = [];
  public onopen: (() => void) | null = null;
  public onmessage: ((evt: { data: unknown }) => void) | null = null;
  public onerror: (() => void) | null = null;
  public onclose: ((evt: { code: number; reason: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.();
    });
  }

  public send(data: Uint8Array): void {
    this.sentFrames.push(new Uint8Array(data));
  }

  public close(code = 1000, reason = ""): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }

  public serverSend(frameBytes: Uint8Array): void {
    this.onmessage?.({ data: frameBytes });
  }
}

describe("@voxdesk/web-sdk", () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    FakeWebSocket.instances = [];
    (globalThis as unknown as { WebSocket: typeof FakeWebSocket }).WebSocket =
      FakeWebSocket;
  });

  afterEach(() => {
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      originalWebSocket;
    vi.restoreAllMocks();
  });

  it("embeds Pipecat frames.proto descriptor and round-trips Audio, Text, Transcription, and Message frames", () => {
    expect(PIPECAT_FRAMES_DESCRIPTOR.package).toBe("pipecat");
    expect(PIPECAT_FRAMES_DESCRIPTOR.messageType.map((m) => m.name)).toEqual([
      "TextFrame",
      "AudioRawFrame",
      "TranscriptionFrame",
      "MessageFrame",
      "Frame",
    ]);

    const rawPcm = float32ToPcm16Bytes(new Float32Array([0, 0.5, -0.5, 1, -1]));
    const audioWire = encodePipecatFrame({
      oneofKind: "audio",
      audio: {
        id: 7,
        name: "mic",
        audio: rawPcm,
        sampleRate: 16000,
        numChannels: 1,
      },
    });
    const decodedAudio = decodePipecatFrame(audioWire);
    expect(decodedAudio?.oneofKind).toBe("audio");
    if (decodedAudio?.oneofKind === "audio") {
      expect(decodedAudio.audio.sampleRate).toBe(16000);
      expect(decodedAudio.audio.numChannels).toBe(1);
      expect(Array.from(decodedAudio.audio.audio)).toEqual(Array.from(rawPcm));
    }

    const msgWire = encodePipecatFrame({
      oneofKind: "message",
      message: {
        data: JSON.stringify({
          type: "transcript",
          role: "assistant",
          text: "Hello from VoxDesk",
        }),
      },
    });
    const decodedMsg = decodePipecatFrame(msgWire);
    expect(decodedMsg?.oneofKind).toBe("message");
    if (decodedMsg?.oneofKind === "message") {
      expect(JSON.parse(decodedMsg.message.data)).toEqual({
        type: "transcript",
        role: "assistant",
        text: "Hello from VoxDesk",
      });
    }
  });

  it("builds 16kHz mono echoCancellation/noiseSuppression media track constraints and converts PCM16 accurately", () => {
    const constraints = buildMediaTrackConstraints();
    expect(constraints.sampleRate).toBe(16000);
    expect(constraints.channelCount).toBe(1);
    expect(constraints.echoCancellation).toBe(true);
    expect(constraints.noiseSuppression).toBe(true);

    const floatIn = new Float32Array([0, 0.25, -0.25, 0.75, -0.75]);
    const pcmBytes = float32ToPcm16Bytes(floatIn);
    const floatOut = pcm16BytesToFloat32(pcmBytes);
    expect(floatOut.length).toBe(floatIn.length);
    for (let i = 0; i < floatIn.length; i++) {
      expect(floatOut[i]).toBeCloseTo(floatIn[i], 3);
    }
  });

  it("connects, sends PCM16 audio + DTMF, receives call_started/transcript/audio/latency events, and stops cleanly", async () => {
    const client = new VoxDeskWebClient();
    const startedEvents: Array<{ callId?: string; transport: string }> = [];
    const transcripts: TranscriptEventPayload[] = [];
    const talkingEvents: string[] = [];
    const latencies: number[] = [];
    const endedEvents: Array<{ code?: number; reason?: string }> = [];

    client.on("call_started", (e) => startedEvents.push(e));
    client.on("transcript", (t) => transcripts.push(t));
    client.on("agent_start_talking", () => talkingEvents.push("start"));
    client.on("agent_stop_talking", () => talkingEvents.push("stop"));
    client.on("latency", (l) => latencies.push(l.totalMs));
    client.on("call_ended", (e) => endedEvents.push(e));

    await client.startCall({
      accessToken: "jwt_single_use_token",
      url: "ws://localhost:8000/telephony/web/ws",
    });

    expect(FakeWebSocket.instances.length).toBe(1);
    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toContain("token=jwt_single_use_token");

    // Simulate server sending call_started message
    socket.serverSend(
      encodePipecatFrame({
        oneofKind: "message",
        message: {
          data: JSON.stringify({ type: "call_started", call_id: "call_web_123" }),
        },
      })
    );
    expect(startedEvents).toEqual([
      { callId: "call_web_123", transport: "ws-protobuf" },
    ]);

    // Send client PCM16 audio frame
    const pcmFrame = float32ToPcm16Bytes(new Float32Array(160).fill(0.2));
    client.sendAudioPcm16(pcmFrame);
    expect(socket.sentFrames.length).toBe(1);
    const clientFrame = decodePipecatFrame(socket.sentFrames[0]);
    expect(clientFrame?.oneofKind).toBe("audio");

    // Mute prevents sending audio
    client.mute();
    expect(client.isMuted()).toBe(true);
    const countAfterMute = socket.sentFrames.length;
    client.sendAudioPcm16(pcmFrame);
    expect(socket.sentFrames.length).toBe(countAfterMute);
    client.unmute();
    expect(client.isMuted()).toBe(false);

    // Send DTMF digit
    client.sendDtmf("5");
    const dtmfFrame = decodePipecatFrame(
      socket.sentFrames[socket.sentFrames.length - 1]
    );
    expect(dtmfFrame?.oneofKind).toBe("message");
    if (dtmfFrame?.oneofKind === "message") {
      expect(JSON.parse(dtmfFrame.message.data)).toEqual({
        type: "dtmf",
        digit: "5",
      });
    }

    // Receive transcript, audio, and latency from server
    socket.serverSend(
      encodePipecatFrame({
        oneofKind: "message",
        message: {
          data: JSON.stringify({
            type: "transcript",
            role: "assistant",
            text: "How can I help you today?",
            final: true,
          }),
        },
      })
    );
    socket.serverSend(
      encodePipecatFrame({
        oneofKind: "audio",
        audio: {
          audio: pcmFrame,
          sampleRate: 16000,
          numChannels: 1,
        },
      })
    );
    socket.serverSend(
      encodePipecatFrame({
        oneofKind: "message",
        message: {
          data: JSON.stringify({ type: "agent_stop_talking" }),
        },
      })
    );
    socket.serverSend(
      encodePipecatFrame({
        oneofKind: "message",
        message: {
          data: JSON.stringify({ type: "latency", total_ms: 142 }),
        },
      })
    );

    expect(transcripts).toEqual([
      {
        role: "assistant",
        text: "How can I help you today?",
        final: true,
      },
    ]);
    expect(talkingEvents).toEqual(["start", "stop"]);
    expect(latencies).toEqual([142]);

    await client.stopCall();
    expect(client.isActive()).toBe(false);
    expect(endedEvents.length).toBeGreaterThanOrEqual(1);
  });
});
