"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { BrowserSoftphone } from "../browser-softphone";
import { mergeCalls, validDtmf } from "../lib";
import { realtimeUrl } from "../realtime";
import type { SoftphoneCall, SoftphoneSession } from "../types";

export function useSoftphone() {
  const clientRef = useRef<BrowserSoftphone | null>(null);
  const [session, setSession] = useState<SoftphoneSession | null>(null);
  const [calls, setCalls] = useState<SoftphoneCall[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const getClient = useCallback(() => {
    if (!clientRef.current) clientRef.current = new BrowserSoftphone((cause) => setError(errorMessage(cause)));
    return clientRef.current;
  }, []);

  useEffect(() => () => clientRef.current?.destroy(), []);

  const execute = useCallback(async <T,>(action: () => Promise<T>, message = ""): Promise<T | null> => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await action();
      if (message) setNotice(message);
      return result;
    } catch (cause) {
      setError(errorMessage(cause));
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshCalls = useCallback(async () => {
    if (!clientRef.current?.currentSession) return;
    try {
      const rows = await getClient().listCalls();
      setCalls((current) => mergeCalls(current, rows));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, [getClient]);

  useEffect(() => {
    if (!session) return;
    void refreshCalls();
    const timer = setInterval(() => void refreshCalls(), 4000);
    return () => clearInterval(timer);
  }, [refreshCalls, session]);

  useEffect(() => {
    if (!session?.websocket_ticket || !session.signaling_url || typeof WebSocket === "undefined") return;
    let stopped = false;
    const socket = new WebSocket(realtimeUrl(session.signaling_url, session.websocket_ticket));
    const heartbeat = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "heartbeat" }));
    }, 30_000);
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data)) as { type?: string; payload?: { call?: SoftphoneCall } };
        if (message.type === "call.updated" && message.payload?.call && !stopped) {
          setCalls((current) => mergeCalls(current, [message.payload!.call!]));
        }
      } catch { setError("A softphone signaling update was invalid; REST reconciliation remains active"); }
    };
    socket.onerror = () => { if (!stopped) setNotice("Softphone realtime signaling is unavailable; REST reconciliation remains active"); };
    return () => {
      stopped = true;
      clearInterval(heartbeat);
      socket.close();
    };
  }, [session?.id, session?.signaling_url, session?.websocket_ticket]);

  const register = useCallback((environmentId: string, deviceLabel: string) => execute(async () => {
    const registered = await getClient().register(environmentId, deviceLabel);
    setSession(registered);
    const rows = await getClient().listCalls();
    setCalls(rows);
    return registered;
  }, "Softphone registered with Twilio; browser registration succeeded"), [execute, getClient]);

  const dial = useCallback((destination: string, callerId: string) => execute(async () => {
    const call = await getClient().dial(destination, callerId);
    setCalls((current) => mergeCalls(current, [call]));
    return call;
  }, "Customer leg created; join the browser leg when ready"), [execute, getClient]);

  const join = useCallback((call: SoftphoneCall) => execute(async () => {
    await getClient().join(call);
    return call;
  }, "Browser join requested; Live status arrives only after the signed agent webhook"), [execute, getClient]);

  const control = useCallback((call: SoftphoneCall, action: "mute" | "hold", enabled: boolean) => execute(async () => {
    const updated = await getClient().control(call.id, action, enabled);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, action === "mute" ? "Participant mute updated" : "Customer hold updated"), [execute, getClient]);

  const end = useCallback((call: SoftphoneCall) => execute(async () => {
    const updated = await getClient().end(call.id);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, "Provider acknowledged the call end"), [execute, getClient]);

  const resume = useCallback((call: SoftphoneCall) => execute(async () => {
    const updated = await getClient().resume(call.id);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, "Customer hold resumed"), [execute, getClient]);

  const recordPause = useCallback((call: SoftphoneCall, paused: boolean) => execute(async () => {
    const updated = await getClient().recordPause(call.id, paused);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, paused ? "Provider verified recording pause" : "Provider verified recording resume"), [execute, getClient]);

  const conference = useCallback((call: SoftphoneCall, destination: string) => execute(async () => {
    const updated = await getClient().conference(call.id, destination);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, "Conference consult leg is active or being created"), [execute, getClient]);

  const transfer = useCallback((call: SoftphoneCall, destination: string, mode: "blind" | "warm") => execute(async () => {
    const updated = await getClient().transfer(call.id, destination, mode);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, mode === "blind" ? "Provider accepted the transfer" : "Consultation leg created; wait for it to answer"), [execute, getClient]);

  const finishTransfer = useCallback((call: SoftphoneCall, action: "complete" | "cancel") => execute(async () => {
    const updated = await getClient().finishTransfer(call.id, action);
    setCalls((current) => mergeCalls(current, [updated]));
    return updated;
  }, action === "complete" ? "Warm transfer completed" : "Consultation cancelled"), [execute, getClient]);

  const sendDtmf = useCallback((call: SoftphoneCall, digits: string) => {
    if (!validDtmf(digits)) {
      setError("Enter 1-32 DTMF digits, * or #");
      return false;
    }
    try {
      getClient().sendDigits(call.id, digits);
      setNotice("DTMF sent on the joined browser leg");
      setError("");
      return true;
    } catch (cause) {
      setError(errorMessage(cause));
      return false;
    }
  }, [getClient]);

  const clearMessages = useCallback(() => { setError(""); setNotice(""); }, []);

  return {
    session, calls, busy, error, notice, setError, setNotice, clearMessages,
    register, dial, join, control, resume, recordPause, conference, end, transfer, finishTransfer, sendDtmf, refreshCalls,
  };
}
