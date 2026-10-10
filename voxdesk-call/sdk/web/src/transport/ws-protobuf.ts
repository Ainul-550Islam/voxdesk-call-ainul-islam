/**
 * AUTO-GENERATED FROM pipecat-ai 0.0.94 `pipecat/frames/frames.proto`
 * (`pipecat.frames.protobufs.frames_pb2.DESCRIPTOR`).
 *
 * Schema source: `/usr/local/lib/python3.13/site-packages/pipecat/frames/frames.proto`
 * Package: `pipecat` (syntax = "proto3")
 */

export const PIPECAT_FRAMES_DESCRIPTOR = {
  name: "frames.proto",
  package: "pipecat",
  syntax: "proto3",
  messageType: [
    {
      name: "TextFrame",
      field: [
        { name: "id", number: 1, label: "LABEL_OPTIONAL", type: "TYPE_UINT64" },
        { name: "name", number: 2, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
        { name: "text", number: 3, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
      ],
    },
    {
      name: "AudioRawFrame",
      field: [
        { name: "id", number: 1, label: "LABEL_OPTIONAL", type: "TYPE_UINT64" },
        { name: "name", number: 2, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
        { name: "audio", number: 3, label: "LABEL_OPTIONAL", type: "TYPE_BYTES" },
        { name: "sample_rate", number: 4, label: "LABEL_OPTIONAL", type: "TYPE_UINT32" },
        { name: "num_channels", number: 5, label: "LABEL_OPTIONAL", type: "TYPE_UINT32" },
        { name: "pts", number: 6, label: "LABEL_OPTIONAL", type: "TYPE_UINT64", proto3Optional: true },
      ],
    },
    {
      name: "TranscriptionFrame",
      field: [
        { name: "id", number: 1, label: "LABEL_OPTIONAL", type: "TYPE_UINT64" },
        { name: "name", number: 2, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
        { name: "text", number: 3, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
        { name: "user_id", number: 4, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
        { name: "timestamp", number: 5, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
      ],
    },
    {
      name: "MessageFrame",
      field: [
        { name: "data", number: 1, label: "LABEL_OPTIONAL", type: "TYPE_STRING" },
      ],
    },
    {
      name: "Frame",
      field: [
        { name: "text", number: 1, label: "LABEL_OPTIONAL", type: "TYPE_MESSAGE", typeName: ".pipecat.TextFrame", oneofIndex: 0 },
        { name: "audio", number: 2, label: "LABEL_OPTIONAL", type: "TYPE_MESSAGE", typeName: ".pipecat.AudioRawFrame", oneofIndex: 0 },
        { name: "transcription", number: 3, label: "LABEL_OPTIONAL", type: "TYPE_MESSAGE", typeName: ".pipecat.TranscriptionFrame", oneofIndex: 0 },
        { name: "message", number: 4, label: "LABEL_OPTIONAL", type: "TYPE_MESSAGE", typeName: ".pipecat.MessageFrame", oneofIndex: 0 },
      ],
    },
  ],
} as const;

export interface PipecatTextFrame {
  id?: number;
  name?: string;
  text: string;
}

export interface PipecatAudioRawFrame {
  id?: number;
  name?: string;
  audio: Uint8Array;
  sampleRate: number;
  numChannels: number;
  pts?: number;
}

export interface PipecatTranscriptionFrame {
  id?: number;
  name?: string;
  text: string;
  userId: string;
  timestamp: string;
}

export interface PipecatMessageFrame {
  data: string;
}

export type PipecatFrame =
  | { oneofKind: "text"; text: PipecatTextFrame }
  | { oneofKind: "audio"; audio: PipecatAudioRawFrame }
  | { oneofKind: "transcription"; transcription: PipecatTranscriptionFrame }
  | { oneofKind: "message"; message: PipecatMessageFrame };

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder("utf-8");

function writeVarint(value: number): Uint8Array {
  const out: number[] = [];
  let v = value >>> 0;
  while (v > 0x7f) {
    out.push((v & 0x7f) | 0x80);
    v = v >>> 7;
  }
  out.push(v & 0x7f);
  return new Uint8Array(out);
}

function readVarint(buf: Uint8Array, offset: number): { value: number; next: number } {
  let result = 0;
  let shift = 0;
  let pos = offset;
  while (pos < buf.length) {
    const byte = buf[pos++];
    result |= (byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) {
      return { value: result >>> 0, next: pos };
    }
    shift += 7;
  }
  return { value: result >>> 0, next: pos };
}

function concatUint8Arrays(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((acc, c) => acc + c.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
}

function writeLengthDelimitedField(fieldNumber: number, payload: Uint8Array): Uint8Array {
  const tag = writeVarint((fieldNumber << 3) | 2);
  const len = writeVarint(payload.byteLength);
  return concatUint8Arrays([tag, len, payload]);
}

function writeVarintField(fieldNumber: number, value: number): Uint8Array {
  if (!value) return new Uint8Array(0);
  const tag = writeVarint((fieldNumber << 3) | 0);
  const val = writeVarint(value);
  return concatUint8Arrays([tag, val]);
}

function writeStringField(fieldNumber: number, value: string | undefined): Uint8Array {
  if (!value) return new Uint8Array(0);
  return writeLengthDelimitedField(fieldNumber, textEncoder.encode(value));
}

export function encodePipecatFrame(frame: PipecatFrame): Uint8Array {
  switch (frame.oneofKind) {
    case "text": {
      const inner = concatUint8Arrays([
        writeVarintField(1, frame.text.id ?? 0),
        writeStringField(2, frame.text.name),
        writeStringField(3, frame.text.text),
      ]);
      return writeLengthDelimitedField(1, inner);
    }
    case "audio": {
      const inner = concatUint8Arrays([
        writeVarintField(1, frame.audio.id ?? 0),
        writeStringField(2, frame.audio.name),
        writeLengthDelimitedField(3, frame.audio.audio),
        writeVarintField(4, frame.audio.sampleRate),
        writeVarintField(5, frame.audio.numChannels),
        frame.audio.pts !== undefined ? writeVarintField(6, frame.audio.pts) : new Uint8Array(0),
      ]);
      return writeLengthDelimitedField(2, inner);
    }
    case "transcription": {
      const inner = concatUint8Arrays([
        writeVarintField(1, frame.transcription.id ?? 0),
        writeStringField(2, frame.transcription.name),
        writeStringField(3, frame.transcription.text),
        writeStringField(4, frame.transcription.userId),
        writeStringField(5, frame.transcription.timestamp),
      ]);
      return writeLengthDelimitedField(3, inner);
    }
    case "message": {
      const inner = writeStringField(1, frame.message.data);
      return writeLengthDelimitedField(4, inner);
    }
  }
}

export function decodePipecatFrame(data: Uint8Array | ArrayBuffer): PipecatFrame | null {
  const buf = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (buf.byteLength === 0) return null;

  const outerTag = readVarint(buf, 0);
  const outerField = outerTag.value >>> 3;
  const outerWire = outerTag.value & 0x07;
  if (outerWire !== 2) return null;

  const outerLen = readVarint(buf, outerTag.next);
  const inner = buf.subarray(outerLen.next, outerLen.next + outerLen.value);

  if (outerField === 1) {
    let pos = 0;
    let id = 0;
    let name = "";
    let text = "";
    while (pos < inner.byteLength) {
      const t = readVarint(inner, pos);
      pos = t.next;
      const fno = t.value >>> 3;
      const wt = t.value & 0x07;
      if (wt === 0) {
        const v = readVarint(inner, pos);
        pos = v.next;
        if (fno === 1) id = v.value;
      } else if (wt === 2) {
        const l = readVarint(inner, pos);
        pos = l.next;
        const s = textDecoder.decode(inner.subarray(pos, pos + l.value));
        pos += l.value;
        if (fno === 2) name = s;
        if (fno === 3) text = s;
      } else {
        break;
      }
    }
    return { oneofKind: "text", text: { id, name, text } };
  }

  if (outerField === 2) {
    let pos = 0;
    let id = 0;
    let name = "";
    let audio = new Uint8Array(0);
    let sampleRate = 16000;
    let numChannels = 1;
    let pts: number | undefined;
    while (pos < inner.byteLength) {
      const t = readVarint(inner, pos);
      pos = t.next;
      const fno = t.value >>> 3;
      const wt = t.value & 0x07;
      if (wt === 0) {
        const v = readVarint(inner, pos);
        pos = v.next;
        if (fno === 1) id = v.value;
        if (fno === 4) sampleRate = v.value;
        if (fno === 5) numChannels = v.value;
        if (fno === 6) pts = v.value;
      } else if (wt === 2) {
        const l = readVarint(inner, pos);
        pos = l.next;
        const sub = inner.subarray(pos, pos + l.value);
        pos += l.value;
        if (fno === 2) name = textDecoder.decode(sub);
        if (fno === 3) audio = new Uint8Array(sub);
      } else {
        break;
      }
    }
    return {
      oneofKind: "audio",
      audio: { id, name, audio, sampleRate, numChannels, pts },
    };
  }

  if (outerField === 3) {
    let pos = 0;
    let id = 0;
    let name = "";
    let text = "";
    let userId = "";
    let timestamp = "";
    while (pos < inner.byteLength) {
      const t = readVarint(inner, pos);
      pos = t.next;
      const fno = t.value >>> 3;
      const wt = t.value & 0x07;
      if (wt === 0) {
        const v = readVarint(inner, pos);
        pos = v.next;
        if (fno === 1) id = v.value;
      } else if (wt === 2) {
        const l = readVarint(inner, pos);
        pos = l.next;
        const s = textDecoder.decode(inner.subarray(pos, pos + l.value));
        pos += l.value;
        if (fno === 2) name = s;
        if (fno === 3) text = s;
        if (fno === 4) userId = s;
        if (fno === 5) timestamp = s;
      } else {
        break;
      }
    }
    return {
      oneofKind: "transcription",
      transcription: { id, name, text, userId, timestamp },
    };
  }

  if (outerField === 4) {
    let pos = 0;
    let dataStr = "";
    while (pos < inner.byteLength) {
      const t = readVarint(inner, pos);
      pos = t.next;
      const fno = t.value >>> 3;
      const wt = t.value & 0x07;
      if (wt === 2) {
        const l = readVarint(inner, pos);
        pos = l.next;
        const s = textDecoder.decode(inner.subarray(pos, pos + l.value));
        pos += l.value;
        if (fno === 1) dataStr = s;
      } else {
        break;
      }
    }
    return { oneofKind: "message", message: { data: dataStr } };
  }

  return null;
}

export interface WsProtobufTransportHandlers {
  onOpen?: () => void;
  onClose?: (code: number, reason: string) => void;
  onError?: (error: Error) => void;
  onFrame?: (frame: PipecatFrame) => void;
}

export class WsProtobufTransport {
  private ws: WebSocket | null = null;
  private readonly url: string;
  private readonly handlers: WsProtobufTransportHandlers;

  constructor(url: string, handlers: WsProtobufTransportHandlers = {}) {
    this.url = url;
    this.handlers = handlers;
  }

  public connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      let settled = false;
      const socket = new WebSocket(this.url);
      socket.binaryType = "arraybuffer";
      this.ws = socket;

      socket.onopen = () => {
        settled = true;
        this.handlers.onOpen?.();
        resolve();
      };

      socket.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer || event.data instanceof Uint8Array) {
          const decoded = decodePipecatFrame(event.data);
          if (decoded) {
            this.handlers.onFrame?.(decoded);
          }
        } else if (typeof event.data === "string") {
          try {
            const parsed = JSON.parse(event.data);
            this.handlers.onFrame?.({
              oneofKind: "message",
              message: { data: JSON.stringify(parsed) },
            });
          } catch {
            // ignore non-JSON text frame
          }
        }
      };

      socket.onerror = () => {
        const err = new Error("WebSocket protobuf transport error");
        this.handlers.onError?.(err);
        if (!settled) {
          settled = true;
          reject(err);
        }
      };

      socket.onclose = (event: CloseEvent) => {
        this.handlers.onClose?.(event.code ?? 1000, event.reason ?? "");
      };
    });
  }

  public sendAudio(
    pcm16Bytes: Uint8Array,
    sampleRate: number = 16000,
    numChannels: number = 1
  ): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const encoded = encodePipecatFrame({
      oneofKind: "audio",
      audio: {
        audio: pcm16Bytes,
        sampleRate,
        numChannels,
      },
    });
    this.ws.send(encoded);
  }

  public sendMessage(payload: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const encoded = encodePipecatFrame({
      oneofKind: "message",
      message: { data: JSON.stringify(payload) },
    });
    this.ws.send(encoded);
  }

  public close(code: number = 1000, reason: string = "client_stop"): void {
    if (this.ws) {
      try {
        this.ws.close(code, reason);
      } catch {
        // ignore close error
      }
      this.ws = null;
    }
  }
}
