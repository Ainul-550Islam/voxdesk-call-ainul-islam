/**
 * `@voxdesk/react` (`sdk/react/src/index.tsx`)
 *
 * Provides:
 * - `useVoxDeskCall()` hook wrapping `VoxDeskWebClient` from `@voxdesk/web-sdk`
 * - `<VoxDeskCallButton />` ready-to-drop browser voice call button + live transcript & latency badge
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  LatencyEventPayload,
  StartCallOptions,
  TranscriptEventPayload,
  VoxDeskWebClient,
} from "../../web/src/index";

export interface UseVoxDeskCallResult {
  status: "idle" | "connecting" | "connected" | "ended" | "error";
  callId: string | null;
  muted: boolean;
  agentTalking: boolean;
  transcripts: TranscriptEventPayload[];
  latency: LatencyEventPayload | null;
  error: Error | null;
  startCall: (options: StartCallOptions) => Promise<void>;
  stopCall: () => Promise<void>;
  toggleMute: () => void;
  sendDtmf: (digit: string) => void;
}

export function useVoxDeskCall(): UseVoxDeskCallResult {
  const clientRef = useRef<VoxDeskWebClient | null>(null);
  const [status, setStatus] = useState<
    "idle" | "connecting" | "connected" | "ended" | "error"
  >("idle");
  const [callId, setCallId] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [agentTalking, setAgentTalking] = useState(false);
  const [transcripts, setTranscripts] = useState<TranscriptEventPayload[]>([]);
  const [latency, setLatency] = useState<LatencyEventPayload | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const client = new VoxDeskWebClient();
    clientRef.current = client;

    const onStarted = (payload: { callId?: string }) => {
      setStatus("connected");
      if (payload.callId) {
        setCallId(payload.callId);
      }
    };
    const onEnded = () => {
      setStatus("ended");
      setAgentTalking(false);
    };
    const onAgentStart = () => setAgentTalking(true);
    const onAgentStop = () => setAgentTalking(false);
    const onTranscript = (item: TranscriptEventPayload) => {
      setTranscripts((prev) => [...prev, item]);
    };
    const onLatency = (lat: LatencyEventPayload) => {
      setLatency(lat);
    };
    const onError = (err: Error) => {
      setError(err);
      setStatus("error");
    };

    client.on("call_started", onStarted);
    client.on("call_ended", onEnded);
    client.on("agent_start_talking", onAgentStart);
    client.on("agent_stop_talking", onAgentStop);
    client.on("transcript", onTranscript);
    client.on("latency", onLatency);
    client.on("error", onError);

    return () => {
      client.off("call_started", onStarted);
      client.off("call_ended", onEnded);
      client.off("agent_start_talking", onAgentStart);
      client.off("agent_stop_talking", onAgentStop);
      client.off("transcript", onTranscript);
      client.off("latency", onLatency);
      client.off("error", onError);
      void client.stopCall();
    };
  }, []);

  const startCall = useCallback(async (options: StartCallOptions) => {
    if (!clientRef.current) return;
    setError(null);
    setTranscripts([]);
    setLatency(null);
    setStatus("connecting");
    try {
      await clientRef.current.startCall(options);
      setStatus("connected");
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
      setStatus("error");
      throw err;
    }
  }, []);

  const stopCall = useCallback(async () => {
    if (!clientRef.current) return;
    await clientRef.current.stopCall();
    setStatus("ended");
  }, []);

  const toggleMute = useCallback(() => {
    if (!clientRef.current) return;
    if (clientRef.current.isMuted()) {
      clientRef.current.unmute();
      setMuted(false);
    } else {
      clientRef.current.mute();
      setMuted(true);
    }
  }, []);

  const sendDtmf = useCallback((digit: string) => {
    clientRef.current?.sendDtmf(digit);
  }, []);

  return {
    status,
    callId,
    muted,
    agentTalking,
    transcripts,
    latency,
    error,
    startCall,
    stopCall,
    toggleMute,
    sendDtmf,
  };
}

export interface VoxDeskCallButtonProps {
  accessToken?: string;
  createSession?: () => Promise<{ accessToken: string; url?: string }>;
  wsUrl?: string;
  label?: string;
  activeLabel?: string;
  className?: string;
}

export const VoxDeskCallButton: React.FC<VoxDeskCallButtonProps> = ({
  accessToken,
  createSession,
  wsUrl,
  label = "Start Voice Call",
  activeLabel = "End Voice Call",
  className,
}) => {
  const {
    status,
    muted,
    agentTalking,
    latency,
    error,
    startCall,
    stopCall,
    toggleMute,
  } = useVoxDeskCall();

  const handleClick = async () => {
    if (status === "connected" || status === "connecting") {
      await stopCall();
      return;
    }
    let token = accessToken ?? "";
    let resolvedUrl = wsUrl;
    if (!token && createSession) {
      const session = await createSession();
      token = session.accessToken;
      resolvedUrl = session.url ?? resolvedUrl;
    }
    await startCall({ accessToken: token, url: resolvedUrl });
  };

  return (
    <div className={className} data-testid="voxdesk-call-button-container">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={status === "connecting"}
        data-testid="voxdesk-call-toggle-btn"
      >
        {status === "connecting"
          ? "Connecting..."
          : status === "connected"
          ? activeLabel
          : label}
      </button>
      {status === "connected" && (
        <button
          type="button"
          onClick={toggleMute}
          data-testid="voxdesk-call-mute-btn"
        >
          {muted ? "Unmute" : "Mute"}
        </button>
      )}
      {status === "connected" && (
        <span data-testid="voxdesk-agent-state">
          {agentTalking ? "Agent speaking" : "Listening"}
        </span>
      )}
      {latency && (
        <span data-testid="voxdesk-latency-badge">{latency.totalMs} ms</span>
      )}
      {error && (
        <span role="alert" data-testid="voxdesk-call-error">
          {error.message}
        </span>
      )}
    </div>
  );
};
