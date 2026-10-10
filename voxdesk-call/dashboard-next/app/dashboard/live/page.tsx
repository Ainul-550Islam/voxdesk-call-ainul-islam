// File: dashboard-next/app/dashboard/live/page.tsx — Supervisor Live Call Monitoring & Takeover Console
"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { BASE_URL } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";
import { SectionHeader } from "@/components/enterprise/SectionHeader";
import {
  LiveCallCard,
  type LiveCallSentiment,
  type LiveCallSummary,
} from "@/components/enterprise/live-call-card";

interface TranscriptItem {
  id: string;
  speaker: "caller" | "agent" | "supervisor";
  text: string;
  is_final: boolean;
  timestamp: number;
}

interface ActiveMonitorSession {
  id: string;
  call_id: string;
  mode: string;
  ws_url?: string;
  ws_token?: string;
}

function buildWsUrl(pathOrUrl: string): string {
  if (pathOrUrl.startsWith("ws://") || pathOrUrl.startsWith("wss://")) {
    return pathOrUrl;
  }
  const httpBase =
    BASE_URL ||
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");
  const wsBase = httpBase.replace(/^http/i, "ws");
  return `${wsBase}${pathOrUrl.startsWith("/") ? "" : "/"}${pathOrUrl}`;
}

async function authedJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const errBody = await res.json();
      if (typeof errBody?.detail === "string") {
        message = errBody.detail;
      } else if (typeof errBody?.detail?.message === "string") {
        message = errBody.detail.message;
      }
    } catch {
      // ignore json parse errors
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

export default function LiveMonitoringPage() {
  const [calls, setCalls] = useState<LiveCallSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCall, setSelectedCall] = useState<LiveCallSummary | null>(null);
  const [activeSession, setActiveSession] = useState<ActiveMonitorSession | null>(null);
  const [isListening, setIsListening] = useState<boolean>(false);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [transcripts, setTranscripts] = useState<TranscriptItem[]>([]);
  const [whisperText, setWhisperText] = useState<string>("");
  const [whisperStatus, setWhisperStatus] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<boolean>(false);

  // Takeover confirmation modal state (required note audited)
  const [takeoverTarget, setTakeoverTarget] = useState<LiveCallSummary | null>(null);
  const [takeoverNote, setTakeoverNote] = useState<string>("");
  const [takeoverDestination, setTakeoverDestination] = useState<string>("");
  const [takeoverSubmitting, setTakeoverSubmitting] = useState<boolean>(false);
  const [takeoverError, setTakeoverError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const heartbeatTimerRef = useRef<number | null>(null);

  const stopAudioAndSocket = useCallback(() => {
    if (heartbeatTimerRef.current !== null) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    if (wsRef.current) {
      try {
        wsRef.current.close(1000, "supervisor_stop");
      } catch {
        // ignore close errors
      }
      wsRef.current = null;
    }
    if (audioCtxRef.current) {
      try {
        void audioCtxRef.current.close();
      } catch {
        // ignore audio close errors
      }
      audioCtxRef.current = null;
    }
    nextPlayTimeRef.current = 0;
    setWsConnected(false);
    setIsListening(false);
  }, []);

  const playPcm16Chunk = useCallback((buffer: ArrayBuffer, sampleRate = 16000) => {
    if (typeof window === "undefined") return;
    const AudioContextCtor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) return;

    if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
      audioCtxRef.current = new AudioContextCtor({ sampleRate });
      nextPlayTimeRef.current = audioCtxRef.current.currentTime;
    }
    const ctx = audioCtxRef.current;
    if (ctx.state === "suspended") {
      void ctx.resume();
    }

    const int16 = new Int16Array(buffer);
    if (int16.length === 0) return;

    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i += 1) {
      float32[i] = Math.max(-1, Math.min(1, int16[i] / 32768));
    }

    const audioBuffer = ctx.createBuffer(1, float32.length, sampleRate);
    audioBuffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    const startAt = Math.max(ctx.currentTime, nextPlayTimeRef.current);
    source.start(startAt);
    nextPlayTimeRef.current = startAt + audioBuffer.duration;
  }, []);

  const fetchActiveCalls = useCallback(async () => {
    try {
      const data = await authedJson<{
        items?: Array<Record<string, unknown>>;
        calls?: Array<Record<string, unknown>>;
      }>("/api/calls?limit=50");
      const rawList = Array.isArray(data?.items)
        ? data.items
        : Array.isArray(data?.calls)
        ? data.calls
        : [];
      const active = rawList
        .filter((item) => {
          const st = String(item.status || "").toLowerCase();
          return st === "in_progress" || st === "ringing" || st === "queued";
        })
        .map((item): LiveCallSummary => {
          const rawSentiment = String(item.sentiment || "neutral").toLowerCase();
          const sentiment: LiveCallSentiment =
            rawSentiment === "positive" ||
            rawSentiment === "negative" ||
            rawSentiment === "escalated"
              ? rawSentiment
              : "neutral";
          return {
            id: String(item.id || ""),
            call_sid: item.call_sid ? String(item.call_sid) : undefined,
            status: String(item.status || "in_progress"),
            direction: item.direction ? String(item.direction) : "inbound",
            caller_number: String(item.caller_number || item.from_number || "Anonymous"),
            to_number: item.to_number ? String(item.to_number) : undefined,
            agent_name: item.agent_name ? String(item.agent_name) : "AI Receptionist",
            provider: item.provider ? String(item.provider) : "twilio",
            started_at: String(item.started_at || item.created_at || new Date().toISOString()),
            duration_sec:
              typeof item.duration_sec === "number" ? item.duration_sec : undefined,
            sentiment,
            takeover_active:
              String(item.transfer_state || "").toLowerCase() === "completed",
            last_utterance: item.summary ? String(item.summary) : undefined,
          };
        });
      setCalls(active);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load active calls");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchActiveCalls();
    const pollId = window.setInterval(() => {
      void fetchActiveCalls();
    }, 5000);
    return () => {
      window.clearInterval(pollId);
      stopAudioAndSocket();
    };
  }, [fetchActiveCalls, stopAudioAndSocket]);

  const connectMonitorSocket = useCallback(
    (sessionInfo: ActiveMonitorSession) => {
      stopAudioAndSocket();
      const wsPath =
        sessionInfo.ws_url ||
        `/ws/monitor/${sessionInfo.call_id}${
          sessionInfo.ws_token ? `?token=${encodeURIComponent(sessionInfo.ws_token)}` : ""
        }`;
      const ws = new WebSocket(buildWsUrl(wsPath));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
        setIsListening(true);
      };

      ws.onmessage = (evt: MessageEvent) => {
        if (evt.data instanceof ArrayBuffer) {
          playPcm16Chunk(evt.data, 16000);
          return;
        }
        if (typeof evt.data === "string") {
          try {
            const msg = JSON.parse(evt.data) as Record<string, unknown>;
            const type = String(msg.type || "");
            if (type === "transcript") {
              const text = String(msg.text || "").trim();
              if (!text) return;
              const speakerRaw = String(msg.speaker || "caller");
              const speaker: "caller" | "agent" | "supervisor" =
                speakerRaw === "agent" || speakerRaw === "supervisor"
                  ? speakerRaw
                  : "caller";
              setTranscripts((prev) => [
                ...prev.slice(-199),
                {
                  id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                  speaker,
                  text,
                  is_final: Boolean(msg.is_final ?? true),
                  timestamp:
                    typeof msg.timestamp === "number"
                      ? msg.timestamp * 1000
                      : Date.now(),
                },
              ]);
            } else if (type === "guidance") {
              const text = String(msg.text || "").trim();
              if (text) {
                setTranscripts((prev) => [
                  ...prev.slice(-199),
                  {
                    id: `${Date.now()}-whisper`,
                    speaker: "supervisor",
                    text: `[Whisper to AI] ${text}`,
                    is_final: true,
                    timestamp: Date.now(),
                  },
                ]);
              }
            } else if (type === "call_ended") {
              setWhisperStatus("Call has ended.");
              stopAudioAndSocket();
              setActiveSession(null);
              void fetchActiveCalls();
            }
          } catch {
            // ignore malformed JSON frames
          }
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        setIsListening(false);
      };

      heartbeatTimerRef.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "heartbeat" }));
        }
        void authedJson(
          `/api/calls/${sessionInfo.call_id}/monitor/${sessionInfo.id}/heartbeat`,
          { method: "POST" }
        ).catch(() => undefined);
      }, 25000);
    },
    [fetchActiveCalls, playPcm16Chunk, stopAudioAndSocket]
  );

  const handleListenToggle = useCallback(
    async (call: LiveCallSummary) => {
      setError(null);
      setWhisperStatus(null);
      if (isListening && activeSession?.call_id === call.id) {
        setBusyAction(true);
        try {
          stopAudioAndSocket();
          await authedJson(
            `/api/calls/${call.id}/monitor/${activeSession.id}/end`,
            { method: "POST" }
          );
          setActiveSession(null);
        } catch (err) {
          setError(err instanceof Error ? err.message : "Failed to stop monitoring");
        } finally {
          setBusyAction(false);
        }
        return;
      }

      setBusyAction(true);
      try {
        if (activeSession) {
          stopAudioAndSocket();
          await authedJson(
            `/api/calls/${activeSession.call_id}/monitor/${activeSession.id}/end`,
            { method: "POST" }
          ).catch(() => undefined);
        }
        setSelectedCall(call);
        setTranscripts([]);
        const sessionOut = await authedJson<ActiveMonitorSession>(
          `/api/calls/${call.id}/monitor`,
          {
            method: "POST",
            body: JSON.stringify({
              mode: "whisper_ai",
              reason: "Live supervisor monitoring",
            }),
          }
        );
        setActiveSession(sessionOut);
        connectMonitorSocket(sessionOut);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to start live listen");
      } finally {
        setBusyAction(false);
      }
    },
    [activeSession, connectMonitorSocket, isListening, stopAudioAndSocket]
  );

  const handleSelectWhisper = useCallback(
    async (call: LiveCallSummary) => {
      setSelectedCall(call);
      if (!activeSession || activeSession.call_id !== call.id) {
        await handleListenToggle(call);
      }
    },
    [activeSession, handleListenToggle]
  );

  const handleSendWhisper = useCallback(
    async (evt: React.FormEvent) => {
      evt.preventDefault();
      if (!selectedCall) return;
      const trimmed = whisperText.trim();
      if (!trimmed) return;

      setBusyAction(true);
      setWhisperStatus(null);
      try {
        let session = activeSession;
        if (!session || session.call_id !== selectedCall.id) {
          session = await authedJson<ActiveMonitorSession>(
            `/api/calls/${selectedCall.id}/monitor`,
            {
              method: "POST",
              body: JSON.stringify({
                mode: "whisper_ai",
                reason: "Supervisor whisper guidance",
              }),
            }
          );
          setActiveSession(session);
          connectMonitorSocket(session);
        }

        const res = await authedJson<{
          status: string;
          delivery_status: string;
        }>(`/api/calls/${selectedCall.id}/monitor/${session.id}/whisper`, {
          method: "POST",
          body: JSON.stringify({
            text: trimmed,
            target: "agent",
            run_llm: true,
          }),
        });
        setWhisperText("");
        setWhisperStatus(
          `Guidance ${
            res.delivery_status === "DELIVERED"
              ? "injected into live AI context"
              : "queued for agent"
          }`
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to send whisper guidance");
      } finally {
        setBusyAction(false);
      }
    },
    [activeSession, connectMonitorSocket, selectedCall, whisperText]
  );

  const handleOpenTakeoverModal = useCallback((call: LiveCallSummary) => {
    setTakeoverTarget(call);
    setTakeoverNote("");
    setTakeoverDestination("");
    setTakeoverError(null);
  }, []);

  const handleConfirmTakeover = useCallback(
    async (evt: React.FormEvent) => {
      evt.preventDefault();
      if (!takeoverTarget) return;
      const note = takeoverNote.trim();
      if (note.length < 4) {
        setTakeoverError("An audited supervisor note is required before taking over a call.");
        return;
      }

      setTakeoverSubmitting(true);
      setTakeoverError(null);
      try {
        await authedJson(`/api/calls/${takeoverTarget.id}/takeover`, {
          method: "POST",
          body: JSON.stringify({
            reason: note,
            supervisor_destination: takeoverDestination.trim() || undefined,
            ownership: "operator",
          }),
        });
        setTakeoverTarget(null);
        setTakeoverNote("");
        setTakeoverDestination("");
        await fetchActiveCalls();
      } catch (err) {
        setTakeoverError(
          err instanceof Error ? err.message : "Takeover failed; call remained with AI agent."
        );
      } finally {
        setTakeoverSubmitting(false);
      }
    },
    [fetchActiveCalls, takeoverDestination, takeoverNote, takeoverTarget]
  );

  return (
    <div className="space-y-6 p-6 text-slate-100">
      <SectionHeader
        eyebrow="Supervisor Operations • Gate G5"
        title="Live Call Monitoring & Takeover"
        description="Listen to live caller/agent audio in real time, whisper guidance directly into the AI context, or execute an audited human takeover."
        actions={
          <div className="flex items-center gap-3">
            <GlassBadge variant={wsConnected ? "success" : "neutral"} dot={wsConnected}>
              {wsConnected ? "WebAudio Stream Live" : "Monitor Idle"}
            </GlassBadge>
            <button
              type="button"
              onClick={() => void fetchActiveCalls()}
              className="rounded-lg border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-white/[0.1]"
            >
              Refresh Calls
            </button>
          </div>
        }
      />

      {error ? (
        <div
          role="alert"
          className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-200"
        >
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left column: Active calls */}
        <div className="space-y-4 lg:col-span-7">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
              Active Calls ({calls.length})
            </h2>
          </div>

          {loading ? (
            <GlassCard className="p-8 text-center text-sm text-slate-400">
              Loading live calls from gateway…
            </GlassCard>
          ) : calls.length === 0 ? (
            <GlassCard className="p-8 text-center text-sm text-slate-400">
              No active calls in progress right now.
            </GlassCard>
          ) : (
            <div className="space-y-3">
              {calls.map((call) => (
                <LiveCallCard
                  key={call.id}
                  call={call}
                  isListening={isListening && activeSession?.call_id === call.id}
                  isSelected={selectedCall?.id === call.id}
                  busy={busyAction}
                  onListenToggle={handleListenToggle}
                  onSelectWhisper={handleSelectWhisper}
                  onRequestTakeover={handleOpenTakeoverModal}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right column: Live Transcript + Whisper-to-AI */}
        <div className="space-y-4 lg:col-span-5">
          <GlassCard density="compact" className="flex h-[540px] flex-col">
            <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
              <div>
                <h3 className="text-sm font-semibold text-white">
                  Live Transcript & Whisper-to-AI
                </h3>
                <p className="text-xs text-slate-400">
                  {selectedCall
                    ? `Monitoring ${selectedCall.caller_number} (${selectedCall.id.slice(0, 8)})`
                    : "Select a live call to stream audio & transcript"}
                </p>
              </div>
              {selectedCall ? (
                <button
                  type="button"
                  onClick={() => handleOpenTakeoverModal(selectedCall)}
                  className="rounded-lg border border-amber-400/40 bg-amber-500/20 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-500/30"
                >
                  Takeover Call
                </button>
              ) : null}
            </div>

            <div
              className="my-3 flex-1 space-y-2 overflow-y-auto pr-1 text-xs"
              data-testid="live-transcript-stream"
            >
              {transcripts.length === 0 ? (
                <p className="py-12 text-center text-slate-500">
                  {isListening
                    ? "Listening for live speech turns…"
                    : "Click Listen on any active call to begin streaming PCM16 audio and live transcripts."}
                </p>
              ) : (
                transcripts.map((item) => (
                  <div
                    key={item.id}
                    className={`rounded-lg px-3 py-2 ${
                      item.speaker === "supervisor"
                        ? "border border-amber-400/30 bg-amber-500/10 text-amber-200"
                        : item.speaker === "agent"
                        ? "border border-cyan-400/20 bg-cyan-500/10 text-cyan-100"
                        : "border border-white/10 bg-white/[0.04] text-slate-200"
                    }`}
                  >
                    <div className="mb-0.5 flex items-center justify-between text-[10px] uppercase tracking-wider opacity-75">
                      <span>{item.speaker}</span>
                      <span>{new Date(item.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <p>{item.text}</p>
                  </div>
                ))
              )}
            </div>

            <form onSubmit={handleSendWhisper} className="border-t border-white/[0.08] pt-3">
              <label
                htmlFor="whisper-guidance-input"
                className="mb-1.5 block text-xs font-medium text-slate-300"
              >
                Whisper Guidance to AI (injected as system instruction)
              </label>
              <div className="flex gap-2">
                <input
                  id="whisper-guidance-input"
                  type="text"
                  disabled={!selectedCall || busyAction}
                  value={whisperText}
                  onChange={(e) => setWhisperText(e.target.value)}
                  placeholder="e.g. Offer a 15% retention discount if the caller asks to cancel…"
                  className="flex-1 rounded-lg border border-white/15 bg-slate-950/80 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-cyan-400 focus:outline-none disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={!selectedCall || busyAction || !whisperText.trim()}
                  className="rounded-lg bg-cyan-500 px-4 py-2 text-xs font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:opacity-50"
                >
                  Whisper
                </button>
              </div>
              {whisperStatus ? (
                <p className="mt-1.5 text-[11px] text-emerald-300">{whisperStatus}</p>
              ) : null}
            </form>
          </GlassCard>
        </div>
      </div>

      {/* Takeover confirmation modal with required audited note */}
      {takeoverTarget ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="takeover-modal-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
        >
          <GlassCard density="comfortable" className="w-full max-w-md">
            <h3 id="takeover-modal-title" className="text-base font-semibold text-white">
              Confirm Human Takeover
            </h3>
            <p className="mt-1 text-xs text-slate-300">
              Taking over call with{" "}
              <span className="font-mono font-semibold text-amber-200">
                {takeoverTarget.caller_number}
              </span>{" "}
              will replace the AI media stream with a live supervisor bridge and record an
              immutable enterprise audit entry.
            </p>

            <form onSubmit={handleConfirmTakeover} className="mt-4 space-y-4">
              <div>
                <label
                  htmlFor="takeover-destination-input"
                  className="mb-1 block text-xs font-medium text-slate-300"
                >
                  Supervisor Destination (E.164 number or WebRTC client, optional if default configured)
                </label>
                <input
                  id="takeover-destination-input"
                  type="text"
                  value={takeoverDestination}
                  onChange={(e) => setTakeoverDestination(e.target.value)}
                  placeholder="+15550109999"
                  className="w-full rounded-lg border border-white/15 bg-slate-950/90 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-amber-400 focus:outline-none"
                />
              </div>

              <div>
                <label
                  htmlFor="takeover-audit-note"
                  className="mb-1 block text-xs font-medium text-amber-200"
                >
                  Required Audit Note / Reason *
                </label>
                <textarea
                  id="takeover-audit-note"
                  required
                  rows={3}
                  value={takeoverNote}
                  onChange={(e) => setTakeoverNote(e.target.value)}
                  placeholder="Describe why human intervention is required (recorded in AuditLog)…"
                  className="w-full rounded-lg border border-white/15 bg-slate-950/90 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-amber-400 focus:outline-none"
                />
              </div>

              {takeoverError ? (
                <p className="text-xs text-rose-300">{takeoverError}</p>
              ) : null}

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  disabled={takeoverSubmitting}
                  onClick={() => setTakeoverTarget(null)}
                  className="rounded-lg border border-white/10 bg-white/[0.05] px-4 py-2 text-xs font-medium text-slate-300 hover:bg-white/[0.1]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={takeoverSubmitting || takeoverNote.trim().length < 4}
                  className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50"
                >
                  {takeoverSubmitting ? "Bridging…" : "Confirm & Take Over"}
                </button>
              </div>
            </form>
          </GlassCard>
        </div>
      ) : null}
    </div>
  );
}
