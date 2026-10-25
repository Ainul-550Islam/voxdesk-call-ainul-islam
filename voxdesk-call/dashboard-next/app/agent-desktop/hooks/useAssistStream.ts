"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { realtimeUrl, type StreamTicket } from "../realtime";
import type {
  AssistAlert,
  AssistSuggestion,
  AssistTurn,
  ComplianceAdherence,
  ComplianceScript,
  WrapupDraft,
} from "../types";

export type AssistFeedback = "accepted" | "dismissed" | "helpful" | "not_helpful";

export function useAssistStream(onError?: (message: string) => void) {
  const [assistCallId, setAssistCallId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<AssistTurn[]>([]);
  const [suggestions, setSuggestions] = useState<AssistSuggestion[]>([]);
  const [alerts, setAlerts] = useState<AssistAlert[]>([]);
  const [scripts, setScripts] = useState<ComplianceScript[]>([]);
  const [adherence, setAdherence] = useState<ComplianceAdherence[]>([]);
  const [error, setLocalError] = useState("");
  const [transport, setTransport] = useState<"connecting" | "realtime" | "polling">("polling");

  const fail = useCallback((cause: unknown) => {
    const message = errorMessage(cause);
    setLocalError(message);
    onError?.(message);
  }, [onError]);

  const load = useCallback(async () => {
    if (!assistCallId) return;
    try {
      const [suggestionResponse, alertResponse, turnResponse, adherenceResponse, scriptResponse] = await Promise.all([
        api<{ suggestions: AssistSuggestion[] }>(`/api/cc2/assist/calls/${assistCallId}/suggestions`),
        api<{ alerts: AssistAlert[] }>(`/api/cc2/assist/calls/${assistCallId}/alerts`),
        api<{ turns: AssistTurn[] }>(`/api/cc2/assist/calls/${assistCallId}/turns`),
        api<{ adherence: ComplianceAdherence[] }>(`/api/cc2/assist/calls/${assistCallId}/adherence`),
        api<{ scripts: ComplianceScript[] }>("/api/cc2/assist/compliance-scripts"),
      ]);
      setSuggestions(suggestionResponse.suggestions);
      setAlerts(alertResponse.alerts);
      setTranscript(turnResponse.turns);
      setAdherence(adherenceResponse.adherence);
      setScripts(scriptResponse.scripts);
    } catch (cause) { fail(cause); }
  }, [assistCallId, fail]);

  useEffect(() => {
    if (!assistCallId) return;
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    setTransport("connecting");
    void load();
    const pollingTimer = setInterval(() => void load(), 4000);

    const scheduleReconnect = () => {
      if (stopped) return;
      setTransport("polling");
      const delay = Math.min(10_000, 1000 * 2 ** attempt);
      attempt += 1;
      reconnectTimer = setTimeout(() => void connect(), delay);
    };

    const connect = async () => {
      if (stopped) return;
      if (typeof WebSocket === "undefined") { setTransport("polling"); return; }
      try {
        const ticket = await api<StreamTicket>(`/api/cc2/assist/calls/${assistCallId}/stream-ticket`);
        if (stopped) return;
        socket = new WebSocket(realtimeUrl(ticket.websocket_url, ticket.ticket));
        socket.onopen = () => {
          if (stopped) return;
          attempt = 0;
          setTransport("realtime");
        };
        socket.onmessage = (event) => {
          try {
            const message = JSON.parse(String(event.data)) as {
              type?: string;
              payload?: {
                turns: AssistTurn[];
                suggestions: AssistSuggestion[];
                alerts: AssistAlert[];
              };
            };
            if (message.type === "assist.snapshot" && message.payload) {
              setTranscript(message.payload.turns);
              setSuggestions(message.payload.suggestions);
              setAlerts(message.payload.alerts);
              setLocalError("");
            }
          } catch { setTransport("polling"); }
        };
        socket.onerror = () => { if (!stopped) setTransport("polling"); };
        socket.onclose = scheduleReconnect;
      } catch {
        scheduleReconnect();
      }
    };
    void connect();
    return () => {
      stopped = true;
      clearInterval(pollingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [assistCallId, load]);

  const start = useCallback(async (callId: string) => {
    try {
      await api(`/api/cc2/assist/calls/${callId}/start`, { method: "POST", body: {} });
      setAssistCallId(callId);
      setTranscript([]);
      setSuggestions([]);
      setAlerts([]);
      setAdherence([]);
      setLocalError("");
      return true;
    } catch (cause) { fail(cause); return false; }
  }, [fail]);

  const stop = useCallback(async () => {
    if (!assistCallId) return false;
    try {
      await api(`/api/cc2/assist/calls/${assistCallId}/stop`, { method: "POST" });
      setAssistCallId(null);
      setTransport("polling");
      return true;
    } catch (cause) { fail(cause); return false; }
  }, [assistCallId, fail]);

  const feedback = useCallback(async (suggestion: AssistSuggestion, value: AssistFeedback) => {
    try {
      const response = await api<{ suggestion: AssistSuggestion }>(
        `/api/cc2/assist/suggestions/${suggestion.id}/feedback`,
        { method: "POST", body: { feedback: value } },
      );
      setSuggestions((current) => current.map((row) => row.id === suggestion.id ? response.suggestion : row));
      return true;
    } catch (cause) { fail(cause); return false; }
  }, [fail]);

  const createWrapup = useCallback(async (callId: string) => {
    try {
      const response = await api<{ wrapup: WrapupDraft }>(`/api/cc2/assist/calls/${callId}/wrapup`, { method: "POST" });
      return response.wrapup;
    } catch (cause) { fail(cause); return null; }
  }, [fail]);

  return {
    assistCallId,
    transcript,
    suggestions,
    alerts,
    scripts,
    adherence,
    error,
    transport,
    start,
    stop,
    feedback,
    createWrapup,
    reload: load,
  };
}
