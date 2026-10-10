// File: dashboard-next/components/enterprise/live-call-card.tsx — Single live call card with status, duration, sentiment, and supervisor controls
"use client";

import React, { useEffect, useState } from "react";
import { GlassBadge, type GlassBadgeVariant } from "./GlassBadge";
import { GlassCard } from "./GlassCard";

export type LiveCallSentiment = "positive" | "neutral" | "negative" | "escalated";

export interface LiveCallSummary {
  id: string;
  call_sid?: string;
  status: string;
  direction?: "inbound" | "outbound" | string;
  caller_number: string;
  to_number?: string;
  agent_name?: string;
  provider?: string;
  started_at: string;
  duration_sec?: number;
  sentiment: LiveCallSentiment;
  active_supervisors?: number;
  takeover_active?: boolean;
  last_utterance?: string;
}

export interface LiveCallCardProps {
  call: LiveCallSummary;
  isListening: boolean;
  isSelected: boolean;
  busy?: boolean;
  onListenToggle: (call: LiveCallSummary) => void;
  onSelectWhisper: (call: LiveCallSummary) => void;
  onRequestTakeover: (call: LiveCallSummary) => void;
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function sentimentVariant(sentiment: LiveCallSentiment): GlassBadgeVariant {
  switch (sentiment) {
    case "positive":
      return "success";
    case "negative":
      return "warning";
    case "escalated":
      return "danger";
    default:
      return "info";
  }
}

function statusVariant(status: string): GlassBadgeVariant {
  const norm = status.toLowerCase();
  if (norm === "in_progress" || norm === "active") return "success";
  if (norm === "ringing" || norm === "queued") return "info";
  if (norm.includes("takeover")) return "warning";
  if (norm === "failed" || norm === "cancelled") return "danger";
  return "neutral";
}

export function LiveCallCard({
  call,
  isListening,
  isSelected,
  busy = false,
  onListenToggle,
  onSelectWhisper,
  onRequestTakeover,
}: LiveCallCardProps) {
  const [elapsedSec, setElapsedSec] = useState<number>(() => {
    if (typeof call.duration_sec === "number" && call.duration_sec > 0) {
      return call.duration_sec;
    }
    const startedMs = Date.parse(call.started_at);
    if (!Number.isNaN(startedMs)) {
      return Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
    }
    return 0;
  });

  useEffect(() => {
    const startedMs = Date.parse(call.started_at);
    const timer = window.setInterval(() => {
      if (!Number.isNaN(startedMs)) {
        setElapsedSec(Math.max(0, Math.floor((Date.now() - startedMs) / 1000)));
      } else {
        setElapsedSec((prev) => prev + 1);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [call.started_at]);

  return (
    <GlassCard
      density="compact"
      className={`transition-all ${
        isListening
          ? "ring-1 ring-emerald-400/50"
          : isSelected
          ? "ring-1 ring-cyan-400/50"
          : ""
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold text-white">
              {call.caller_number || "Unknown Caller"}
            </span>
            {call.to_number ? (
              <span className="text-xs text-slate-400">
                → {call.to_number}
              </span>
            ) : null}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span>Agent: {call.agent_name || "AI Receptionist"}</span>
            <span>•</span>
            <span className="uppercase">{call.provider || "twilio"}</span>
            <span>•</span>
            <span
              className="font-mono text-slate-300"
              data-testid={`call-duration-${call.id}`}
            >
              {formatDuration(elapsedSec)}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <GlassBadge variant={statusVariant(call.status)} dot>
            {call.takeover_active ? "HUMAN TAKEOVER" : call.status.replace(/_/g, " ")}
          </GlassBadge>
          <GlassBadge variant={sentimentVariant(call.sentiment)}>
            Sentiment: {call.sentiment}
          </GlassBadge>
        </div>
      </div>

      {call.last_utterance ? (
        <p className="mt-3 line-clamp-2 rounded-lg border border-white/[0.06] bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
          “{call.last_utterance}”
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-3">
        <span className="text-[11px] text-slate-400">
          Supervisors listening: {call.active_supervisors ?? (isListening ? 1 : 0)} / 5
        </span>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onListenToggle(call)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              isListening
                ? "border border-emerald-400/40 bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/30"
                : "border border-white/10 bg-white/[0.05] text-slate-200 hover:bg-white/[0.1]"
            } disabled:opacity-50`}
          >
            {isListening ? "Stop Listening" : "Listen"}
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => onSelectWhisper(call)}
            className="rounded-lg border border-cyan-400/30 bg-cyan-500/15 px-3 py-1.5 text-xs font-medium text-cyan-200 transition hover:bg-cyan-500/25 disabled:opacity-50"
          >
            Whisper to AI
          </button>

          <button
            type="button"
            disabled={busy || Boolean(call.takeover_active)}
            onClick={() => onRequestTakeover(call)}
            className="rounded-lg border border-amber-400/40 bg-amber-500/20 px-3 py-1.5 text-xs font-semibold text-amber-200 transition hover:bg-amber-500/30 disabled:opacity-50"
          >
            {call.takeover_active ? "Taken Over" : "Takeover"}
          </button>
        </div>
      </div>
    </GlassCard>
  );
}

export default LiveCallCard;
