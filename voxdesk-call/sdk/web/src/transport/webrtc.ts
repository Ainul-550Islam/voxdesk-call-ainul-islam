/**
 * SmallWebRTC signaling client (Phase 2 upgrade path) (`sdk/web/src/transport/webrtc.ts`).
 *
 * Negotiates an SDP offer/answer with `POST /telephony/web/offer` using the
 * single-use web-call `accessToken`.
 */

export interface SmallWebRTCOptions {
  offerUrl?: string;
  accessToken: string;
  iceServers?: RTCIceServer[];
  onTrack?: (stream: MediaStream) => void;
  onDataMessage?: (message: Record<string, unknown>) => void;
  onDisconnected?: () => void;
}

export class SmallWebRTCClient {
  private pc: RTCPeerConnection | null = null;
  private dc: RTCDataChannel | null = null;
  private localStream: MediaStream | null = null;
  private readonly options: SmallWebRTCOptions;

  constructor(options: SmallWebRTCOptions) {
    this.options = options;
  }

  public async connect(localStream?: MediaStream | null): Promise<{ callId: string }> {
    if (typeof RTCPeerConnection === "undefined") {
      throw new Error("RTCPeerConnection is not available in this browser environment.");
    }
    this.localStream = localStream ?? null;
    this.pc = new RTCPeerConnection({
      iceServers: this.options.iceServers ?? [{ urls: "stun:stun.l.google.com:19302" }],
    });

    if (this.localStream) {
      for (const track of this.localStream.getTracks()) {
        this.pc.addTrack(track, this.localStream);
      }
    } else {
      this.pc.addTransceiver("audio", { direction: "sendrecv" });
    }

    this.pc.ontrack = (event: RTCTrackEvent) => {
      if (event.streams && event.streams[0]) {
        this.options.onTrack?.(event.streams[0]);
      }
    };

    this.pc.onconnectionstatechange = () => {
      if (
        this.pc &&
        (this.pc.connectionState === "disconnected" ||
          this.pc.connectionState === "failed" ||
          this.pc.connectionState === "closed")
      ) {
        this.options.onDisconnected?.();
      }
    };

    this.dc = this.pc.createDataChannel("signalling");
    this.dc.onmessage = (evt: MessageEvent) => {
      if (typeof evt.data === "string") {
        try {
          this.options.onDataMessage?.(JSON.parse(evt.data));
        } catch {
          // ignore malformed data channel payload
        }
      }
    };

    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);

    const endpoint = this.options.offerUrl ?? "/telephony/web/offer";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        access_token: this.options.accessToken,
        sdp: offer.sdp ?? "",
        type: "offer",
      }),
    });

    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}));
      const msg =
        errBody?.detail?.message ||
        errBody?.message ||
        `SmallWebRTC signaling failed with HTTP ${response.status}`;
      throw new Error(msg);
    }

    const answer = await response.json();
    await this.pc.setRemoteDescription({
      type: "answer",
      sdp: answer.sdp,
    });

    return { callId: String(answer.call_id ?? "") };
  }

  public sendDataMessage(payload: Record<string, unknown>): void {
    if (this.dc && this.dc.readyState === "open") {
      this.dc.send(JSON.stringify(payload));
    }
  }

  public close(): void {
    if (this.dc) {
      try {
        this.dc.close();
      } catch {
        // ignore
      }
      this.dc = null;
    }
    if (this.pc) {
      try {
        this.pc.close();
      } catch {
        // ignore
      }
      this.pc = null;
    }
  }
}
