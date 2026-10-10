"use client";

/**
 * "Test your agent" browser call panel (`dashboard-next/app/dashboard/agents/[id]/test-call/page.tsx`).
 *
 * Uses `@voxdesk/web-sdk` (`VoxDeskWebClient`) to create a real `POST /api/web-calls`
 * session and connect to `/telephony/web/ws` with microphone capture, live transcript,
 * DTMF keypad, mute/unmute controls, and real-time latency badge.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  LatencyEventPayload,
  TranscriptEventPayload,
  VoxDeskWebClient,
} from "../../../../../../sdk/web/src/index";

interface WebCallBootstrapResponse {
  call_id: string;
  tenant_id: string;
  agent_id: string;
  agent_version: number;
  direction: "web";
  status: string;
  transport: "ws-protobuf" | "small-webrtc";
  url: string;
  access_token: string;
  sample_rate: number;
}

export default function AgentTestCallPage() {
  const params = useParams<{ id: string }>();
  const agentId = typeof params?.id === "string" ? params.id : "";

  const clientRef = useRef<VoxDeskWebClient | null>(null);
  const [status, setStatus] = useState<
    "idle" | "requesting_mic" | "connecting" | "connected" | "ended" | "error"
  >("idle");
  const [callId, setCallId] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [agentTalking, setAgentTalking] = useState(false);
  const [micPermission, setMicPermission] = useState<
    "prompt" | "granted" | "denied"
  >("prompt");
  const [transcripts, setTranscripts] = useState<TranscriptEventPayload[]>([]);
  const [latency, setLatency] = useState<LatencyEventPayload | null>(null);
  const [dynamicVarsJson, setDynamicVarsJson] = useState<string>("{}");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const client = new VoxDeskWebClient();
    clientRef.current = client;

    client.on("call_started", (payload) => {
      setStatus("connected");
      if (payload.callId) {
        setCallId(payload.callId);
      }
    });
    client.on("call_ended", () => {
      setStatus("ended");
      setAgentTalking(false);
    });
    client.on("agent_start_talking", () => setAgentTalking(true));
    client.on("agent_stop_talking", () => setAgentTalking(false));
    client.on("transcript", (item) => {
      setTranscripts((prev) => [...prev, item]);
    });
    client.on("latency", (lat) => {
      setLatency(lat);
    });
    client.on("error", (err) => {
      setErrorMessage(err.message);
      setStatus("error");
    });

    return () => {
      void client.stopCall();
    };
  }, []);

  const handleStartCall = useCallback(async () => {
    if (!agentId) {
      setErrorMessage("Agent ID is required to start a browser test call.");
      setStatus("error");
      return;
    }

    setErrorMessage(null);
    setTranscripts([]);
    setLatency(null);
    setStatus("requesting_mic");

    let parsedVars: Record<string, unknown> = {};
    try {
      parsedVars = JSON.parse(dynamicVarsJson || "{}") as Record<
        string,
        unknown
      >;
    } catch {
      setErrorMessage("Dynamic variables must be valid JSON.");
      setStatus("error");
      return;
    }

    try {
      if (
        typeof navigator !== "undefined" &&
        navigator.mediaDevices &&
        typeof navigator.mediaDevices.getUserMedia === "function"
      ) {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        for (const track of stream.getTracks()) {
          track.stop();
        }
        setMicPermission("granted");
      }
    } catch {
      setMicPermission("denied");
      setErrorMessage(
        "Microphone permission was denied. Please allow microphone access to test your agent."
      );
      setStatus("error");
      return;
    }

    setStatus("connecting");
    try {
      const response = await fetch("/api/web-calls", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Origin:
            typeof window !== "undefined"
              ? window.location.origin
              : "http://localhost:3000",
        },
        body: JSON.stringify({
          agent_id: agentId,
          dynamic_vars: parsedVars,
          metadata: { source: "dashboard_test_call" },
        }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail =
          body?.detail?.message ||
          body?.detail ||
          body?.message ||
          `Failed to create web call (HTTP ${response.status})`;
        throw new Error(String(detail));
      }

      const bootstrap = (await response.json()) as WebCallBootstrapResponse;
      setCallId(bootstrap.call_id);

      await clientRef.current?.startCall({
        accessToken: bootstrap.access_token,
        url: bootstrap.url,
        transport: bootstrap.transport,
        sampleRate: bootstrap.sample_rate ?? 16000,
      });
      setStatus("connected");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }, [agentId, dynamicVarsJson]);

  const handleEndCall = useCallback(async () => {
    await clientRef.current?.stopCall();
    if (callId) {
      await fetch(`/api/web-calls/${encodeURIComponent(callId)}/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "dashboard_user_hangup" }),
      }).catch(() => null);
    }
    setStatus("ended");
  }, [callId]);

  const handleToggleMute = useCallback(() => {
    if (!clientRef.current) return;
    if (clientRef.current.isMuted()) {
      clientRef.current.unmute();
      setMuted(false);
    } else {
      clientRef.current.mute();
      setMuted(true);
    }
  }, []);

  const handleDtmf = useCallback((digit: string) => {
    clientRef.current?.sendDtmf(digit);
  }, []);

  return (
    <div
      className="mx-auto max-w-4xl space-y-6 p-6"
      data-testid="agent-test-call-page"
    >
      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">
            Test Your Agent (Browser Voice Call)
          </h1>
          <p className="text-sm text-slate-400">
            Agent ID: <span className="font-mono text-slate-200">{agentId}</span>
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span
            data-testid="mic-permission-badge"
            className="rounded-full bg-slate-800 px-3 py-1 text-xs font-medium text-slate-300"
          >
            Mic: {micPermission}
          </span>
          <span
            data-testid="latency-badge"
            className="rounded-full bg-indigo-950 px-3 py-1 text-xs font-semibold text-indigo-300"
          >
            Latency: {latency ? `${latency.totalMs} ms` : "—"}
          </span>
        </div>
      </div>

      {errorMessage && (
        <div
          role="alert"
          data-testid="test-call-error"
          className="rounded-lg border border-red-500/40 bg-red-950/50 p-4 text-sm text-red-200"
        >
          {errorMessage}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
            Call Controls
          </h2>

          <div>
            <label
              htmlFor="dynamic-vars-input"
              className="mb-1 block text-xs text-slate-400"
            >
              Dynamic Variables (JSON)
            </label>
            <textarea
              id="dynamic-vars-input"
              data-testid="dynamic-vars-input"
              rows={3}
              value={dynamicVarsJson}
              onChange={(e) => setDynamicVarsJson(e.target.value)}
              disabled={status === "connected" || status === "connecting"}
              className="w-full rounded-md border border-slate-700 bg-slate-950 p-2 font-mono text-xs text-slate-100"
            />
          </div>

          <div className="flex flex-col gap-2">
            {status !== "connected" ? (
              <button
                type="button"
                data-testid="start-test-call-btn"
                onClick={() => void handleStartCall()}
                disabled={
                  status === "connecting" || status === "requesting_mic"
                }
                className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {status === "requesting_mic"
                  ? "Requesting Microphone..."
                  : status === "connecting"
                  ? "Connecting Web Call..."
                  : "Start Browser Call"}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  data-testid="mute-test-call-btn"
                  onClick={handleToggleMute}
                  className="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm font-medium text-slate-100 hover:bg-slate-700"
                >
                  {muted ? "Unmute Microphone" : "Mute Microphone"}
                </button>
                <button
                  type="button"
                  data-testid="end-test-call-btn"
                  onClick={() => void handleEndCall()}
                  className="w-full rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500"
                >
                  End Call
                </button>
              </>
            )}
          </div>

          {status === "connected" && (
            <div className="pt-2">
              <p className="mb-2 text-xs text-slate-400">DTMF Keypad</p>
              <div className="grid grid-cols-3 gap-1.5">
                {[
                  "1",
                  "2",
                  "3",
                  "4",
                  "5",
                  "6",
                  "7",
                  "8",
                  "9",
                  "*",
                  "0",
                  "#",
                ].map((digit) => (
                  <button
                    key={digit}
                    type="button"
                    data-testid={`dtmf-btn-${digit}`}
                    onClick={() => handleDtmf(digit)}
                    className="rounded border border-slate-700 bg-slate-950 py-1.5 font-mono text-xs text-slate-200 hover:bg-slate-800"
                  >
                    {digit}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex flex-col rounded-xl border border-slate-800 bg-slate-900/60 p-4 md:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
              Live Transcript
            </h2>
            <span
              data-testid="call-status-indicator"
              className="text-xs font-medium text-slate-300"
            >
              Status: {status}
              {status === "connected"
                ? agentTalking
                  ? " (Agent speaking)"
                  : " (Listening)"
                : ""}
            </span>
          </div>

          <div
            data-testid="live-transcript-feed"
            className="flex-1 space-y-2 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-sm"
            style={{ minHeight: "280px", maxHeight: "420px" }}
          >
            {transcripts.length === 0 ? (
              <p className="text-xs text-slate-500">
                Transcript turns will stream here in real time once the browser
                voice call starts.
              </p>
            ) : (
              transcripts.map((turn, idx) => (
                <div
                  key={idx}
                  className={`rounded-lg px-3 py-2 ${
                    turn.role === "assistant"
                      ? "bg-indigo-950/60 text-indigo-100"
                      : "bg-slate-800/80 text-slate-100"
                  }`}
                >
                  <span className="mr-2 text-xs font-bold uppercase text-slate-400">
                    {turn.role}:
                  </span>
                  <span>{turn.text}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
