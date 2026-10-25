"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import type { AgentOccupancy, WrapupDraft } from "../types";

export type AgentState = "offline" | "available" | "ringing" | "busy" | "wrap_up" | "away" | "paused" | "disabled";
export type StateReasonCode = "manual" | "break" | "lunch" | "training" | "technical_issue" | "after_call_work" | "shift_end" | "supervisor_change";

interface PresenceResponse {
  presence: { state: AgentState; state_changed_at: string | null; active_count: number; capacity: number };
  reason_code: StateReasonCode | null;
  outcome?: string;
}

export function useAgentState(onError?: (message: string) => void, onNotice?: (message: string) => void) {
  const [state, setState] = useState<AgentState>("offline");
  const [reasonCode, setReasonCode] = useState<StateReasonCode | null>(null);
  const [stateChangedAt, setStateChangedAt] = useState<string | null>(null);
  const [occupancy, setOccupancy] = useState<AgentOccupancy | null>(null);
  const [wrapups, setWrapups] = useState<WrapupDraft[]>([]);

  const loadState = useCallback(async () => {
    try {
      const response = await api<PresenceResponse>("/api/cc2/agent-states/me");
      setState(response.presence.state);
      setReasonCode(response.reason_code);
      setStateChangedAt(response.presence.state_changed_at);
    } catch (cause) { onError?.(errorMessage(cause)); }
  }, [onError]);

  const loadOccupancy = useCallback(async () => {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 60 * 1000);
    try {
      const response = await api<{ agents: AgentOccupancy[] }>(
        `/api/cc2/agent-states/occupancy?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`,
      );
      setOccupancy(response.agents[0] ?? null);
    } catch (cause) { onError?.(errorMessage(cause)); }
  }, [onError]);

  const changeState = useCallback(async (target: AgentState, reason: StateReasonCode = "manual") => {
    try {
      const response = await api<PresenceResponse>("/api/cc2/agent-states/me/state", {
        method: "POST", body: { state: target, reason_code: reason },
      });
      setState(response.presence.state);
      setStateChangedAt(response.presence.state_changed_at);
      setReasonCode(response.reason_code ?? reason);
      onNotice?.(`Agent state changed to ${target}`);
      void loadOccupancy();
      return true;
    } catch (cause) { onError?.(errorMessage(cause)); return false; }
  }, [loadOccupancy, onError, onNotice]);

  const loadWrapups = useCallback(async () => {
    try {
      const response = await api<{ wrapups: WrapupDraft[] }>("/api/cc2/agent-states/wrapups/pending");
      setWrapups(response.wrapups);
    } catch (cause) { onError?.(errorMessage(cause)); }
  }, [onError]);

  useEffect(() => {
    void loadState();
    void loadWrapups();
    void loadOccupancy();
  }, [loadOccupancy, loadState, loadWrapups]);

  const submitWrapup = useCallback(async (callId: string, disposition: string, summary: string, tags: string[]) => {
    try {
      const response = await api<{ wrapup: WrapupDraft }>("/api/cc2/agent-states/wrapups", { method: "POST", body: { call_id: callId, disposition, summary_redacted: summary, tags } });
      setWrapups((current) => current.filter((row) => row.call_id !== callId));
      const crmMessage = response.wrapup.crm_status === "pending" ? "CRM delivery is pending provider acknowledgement" : response.wrapup.crm_status === "written" ? "CRM writeback is acknowledged" : response.wrapup.crm_status === "failed" ? "CRM writeback needs operator review" : "No CRM writeback was configured";
      onNotice?.(`After-call wrap-up approved; ${crmMessage}`);
      void loadOccupancy();
      return true;
    } catch (cause) { onError?.(errorMessage(cause)); return false; }
  }, [loadOccupancy, onError, onNotice]);

  return {
    state,
    reasonCode,
    stateChangedAt,
    occupancy,
    wrapups,
    changeState,
    loadState,
    loadOccupancy,
    loadWrapups,
    submitWrapup,
  };
}
