"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { realtimeUrl, type StreamTicket } from "../realtime";
import type { WallboardSnapshot } from "../types";

export function useWallboard(intervalMs = 5000) {
  const [snapshot, setSnapshot] = useState<WallboardSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [transport, setTransport] = useState<"connecting" | "realtime" | "polling">("connecting");
  const load = useCallback(async () => {
    try {
      const result = await api<WallboardSnapshot>("/api/cc2/acd/wallboard");
      setSnapshot(result);
      setError("");
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    void load();
    const timer = setInterval(() => void load(), intervalMs);
    const connect = async () => {
      if (typeof WebSocket === "undefined") { setTransport("polling"); return; }
      try {
        const ticket = await api<StreamTicket>("/api/cc2/supervisor/stream-ticket");
        if (stopped) return;
        socket = new WebSocket(realtimeUrl(ticket.websocket_url, ticket.ticket));
        socket.onopen = () => { if (!stopped) setTransport("realtime"); };
        socket.onmessage = (event) => {
          try {
            const message = JSON.parse(String(event.data)) as { type?: string; payload?: WallboardSnapshot };
            if (message.type === "wallboard.snapshot" && message.payload) {
              setSnapshot(message.payload);
              setLoading(false);
              setError("");
            }
          } catch { setTransport("polling"); }
        };
        socket.onerror = () => { if (!stopped) setTransport("polling"); };
        socket.onclose = () => { if (!stopped) setTransport("polling"); };
      } catch { if (!stopped) setTransport("polling"); }
    };
    void connect();
    return () => {
      stopped = true;
      clearInterval(timer);
      socket?.close();
    };
  }, [intervalMs, load]);
  return { snapshot, loading, error, transport, reload: load };
}
