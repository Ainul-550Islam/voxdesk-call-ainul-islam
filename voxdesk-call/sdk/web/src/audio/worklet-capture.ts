/**
 * AudioWorklet capture (mono 16 kHz PCM16), echoCancellation/noiseSuppression
 * constraints, and jitter-safe playback queue (`sdk/web/src/audio/worklet-capture.ts`).
 */

export const TARGET_SAMPLE_RATE = 16000;
export const TARGET_CHANNELS = 1;

export interface AudioCaptureConstraints {
  sampleRate?: number;
  channelCount?: number;
  echoCancellation?: boolean;
  noiseSuppression?: boolean;
  autoGainControl?: boolean;
  deviceId?: string;
}

export function buildMediaTrackConstraints(
  overrides: AudioCaptureConstraints = {}
): MediaTrackConstraints {
  const constraints: MediaTrackConstraints = {
    sampleRate: overrides.sampleRate ?? TARGET_SAMPLE_RATE,
    channelCount: overrides.channelCount ?? TARGET_CHANNELS,
    echoCancellation: overrides.echoCancellation ?? true,
    noiseSuppression: overrides.noiseSuppression ?? true,
    autoGainControl: overrides.autoGainControl ?? true,
  };
  if (overrides.deviceId) {
    constraints.deviceId = { exact: overrides.deviceId };
  }
  return constraints;
}

export function float32ToPcm16Bytes(samples: Float32Array): Uint8Array {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const int16 = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    view.setInt16(i * 2, int16, true);
  }
  return new Uint8Array(buffer);
}

export function pcm16BytesToFloat32(pcmBytes: Uint8Array): Float32Array {
  const sampleCount = Math.floor(pcmBytes.byteLength / 2);
  const out = new Float32Array(sampleCount);
  const view = new DataView(
    pcmBytes.buffer,
    pcmBytes.byteOffset,
    sampleCount * 2
  );
  for (let i = 0; i < sampleCount; i++) {
    const s = view.getInt16(i * 2, true);
    out[i] = s < 0 ? s / 0x8000 : s / 0x7fff;
  }
  return out;
}

export function downsampleFloat32(
  input: Float32Array,
  inputRate: number,
  targetRate: number = TARGET_SAMPLE_RATE
): Float32Array {
  if (inputRate === targetRate || inputRate <= 0 || targetRate <= 0) {
    return input;
  }
  const ratio = inputRate / targetRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i++) {
    const srcIdx = Math.min(input.length - 1, Math.floor(i * ratio));
    output[i] = input[srcIdx];
  }
  return output;
}

export const WORKLET_PROCESSOR_SOURCE = `
class VoxDeskPcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._muted = false;
    this.port.onmessage = (e) => {
      if (e.data && typeof e.data.muted === "boolean") {
        this._muted = e.data.muted;
      }
    };
  }
  process(inputs) {
    if (this._muted) return true;
    const input = inputs[0];
    if (input && input[0] && input[0].length > 0) {
      const channel = new Float32Array(input[0]);
      this.port.postMessage({ samples: channel }, [channel.buffer]);
    }
    return true;
  }
}
registerProcessor("voxdesk-pcm-capture", VoxDeskPcmCaptureProcessor);
`;

/**
 * Jitter-safe audio playback queue for 16 kHz mono PCM16 frames from the agent.
 */
export class JitterSafePlaybackQueue {
  private readonly sampleRate: number;
  private readonly minBufferMs: number;
  private queue: Float32Array[] = [];
  private nextScheduledTime = 0;

  constructor(sampleRate: number = TARGET_SAMPLE_RATE, minBufferMs: number = 40) {
    this.sampleRate = sampleRate;
    this.minBufferMs = minBufferMs;
  }

  public enqueuePcm16(
    pcm16Bytes: Uint8Array,
    audioContext?: AudioContext | null
  ): Float32Array {
    const floatSamples = pcm16BytesToFloat32(pcm16Bytes);
    this.queue.push(floatSamples);

    if (
      audioContext &&
      typeof audioContext.createBuffer === "function" &&
      typeof audioContext.createBufferSource === "function" &&
      floatSamples.length > 0
    ) {
      const audioBuffer = audioContext.createBuffer(
        1,
        floatSamples.length,
        this.sampleRate
      );
      if (typeof audioBuffer.copyToChannel === "function") {
        audioBuffer.copyToChannel(new Float32Array(floatSamples), 0);
      } else if (typeof audioBuffer.getChannelData === "function") {
        audioBuffer.getChannelData(0).set(floatSamples);
      }
      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      if (audioContext.destination) {
        source.connect(audioContext.destination);
      }
      const now = audioContext.currentTime || 0;
      const jitterOffset = this.minBufferMs / 1000;
      const startAt = Math.max(now + jitterOffset, this.nextScheduledTime);
      source.start(startAt);
      this.nextScheduledTime = startAt + floatSamples.length / this.sampleRate;
    }

    return floatSamples;
  }

  public clear(): void {
    this.queue = [];
    this.nextScheduledTime = 0;
  }

  public depth(): number {
    return this.queue.length;
  }
}

export class WorkletAudioCapture {
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private muted = false;

  public async start(
    onPcmFrame: (pcm16Bytes: Uint8Array) => void,
    constraints: AudioCaptureConstraints = {}
  ): Promise<void> {
    const trackConstraints = buildMediaTrackConstraints(constraints);
    if (
      typeof navigator !== "undefined" &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function"
    ) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: trackConstraints,
        video: false,
      });
    }

    const AudioCtx =
      typeof window !== "undefined"
        ? window.AudioContext ||
          (window as unknown as { webkitAudioContext?: typeof AudioContext })
            .webkitAudioContext
        : typeof globalThis !== "undefined"
        ? (globalThis as unknown as { AudioContext?: typeof AudioContext })
            .AudioContext
        : undefined;

    if (AudioCtx) {
      this.audioContext = new AudioCtx({ sampleRate: TARGET_SAMPLE_RATE });
      if (
        this.audioContext.audioWorklet &&
        typeof this.audioContext.audioWorklet.addModule === "function" &&
        typeof Blob !== "undefined" &&
        typeof URL !== "undefined" &&
        typeof URL.createObjectURL === "function"
      ) {
        try {
          const blob = new Blob([WORKLET_PROCESSOR_SOURCE], {
            type: "application/javascript",
          });
          const url = URL.createObjectURL(blob);
          await this.audioContext.audioWorklet.addModule(url);
          URL.revokeObjectURL(url);
          if (typeof AudioWorkletNode !== "undefined") {
            this.workletNode = new AudioWorkletNode(
              this.audioContext,
              "voxdesk-pcm-capture"
            );
            this.workletNode.port.onmessage = (evt: MessageEvent) => {
              if (this.muted) return;
              const samples = evt.data?.samples as Float32Array | undefined;
              if (samples && samples.length > 0) {
                const resampled = downsampleFloat32(
                  samples,
                  this.audioContext?.sampleRate ?? TARGET_SAMPLE_RATE,
                  TARGET_SAMPLE_RATE
                );
                onPcmFrame(float32ToPcm16Bytes(resampled));
              }
            };
          }
        } catch {
          // Fallback in environments where Blob worklet URLs are blocked by CSP
        }
      }
    }
  }

  public setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.stream && typeof this.stream.getAudioTracks === "function") {
      for (const track of this.stream.getAudioTracks()) {
        track.enabled = !muted;
      }
    }
    if (this.workletNode?.port) {
      this.workletNode.port.postMessage({ muted });
    }
  }

  public isMuted(): boolean {
    return this.muted;
  }

  public getAudioContext(): AudioContext | null {
    return this.audioContext;
  }

  public async stop(): Promise<void> {
    if (this.workletNode) {
      try {
        this.workletNode.disconnect();
      } catch {
        // ignore
      }
      this.workletNode = null;
    }
    if (this.stream && typeof this.stream.getTracks === "function") {
      for (const track of this.stream.getTracks()) {
        track.stop();
      }
      this.stream = null;
    }
    if (this.audioContext && typeof this.audioContext.close === "function") {
      await this.audioContext.close();
      this.audioContext = null;
    }
  }
}
