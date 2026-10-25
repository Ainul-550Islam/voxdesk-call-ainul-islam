"use client";

import { useState } from "react";
import type { AgentState, StateReasonCode } from "../hooks/useAgentState";
import type { AgentOccupancy } from "../types";
import styles from "../agent-desktop.module.css";

const reasons: Record<string, Array<{ value: StateReasonCode; label: string }>> = {
  available: [{ value: "manual", label: "Ready for contacts" }],
  away: [
    { value: "break", label: "Break" }, { value: "lunch", label: "Lunch" },
    { value: "training", label: "Training / meeting" }, { value: "manual", label: "Other away reason" },
  ],
  paused: [{ value: "technical_issue", label: "Technical issue" }, { value: "supervisor_change", label: "Supervisor pause" }],
  offline: [{ value: "shift_end", label: "End of shift" }, { value: "manual", label: "Sign off" }],
  wrap_up: [{ value: "after_call_work", label: "After-call work" }],
};

export function AgentStateBar({
  state,
  reasonCode,
  stateChangedAt,
  occupancy,
  busy = false,
  onChange,
}: {
  state: AgentState;
  reasonCode: StateReasonCode | null;
  stateChangedAt: string | null;
  occupancy: AgentOccupancy | null;
  busy?: boolean;
  onChange: (state: AgentState, reason: StateReasonCode) => Promise<boolean>;
}) {
  const [target, setTarget] = useState<AgentState>("available");
  const [reason, setReason] = useState<StateReasonCode>("manual");
  const [saving, setSaving] = useState(false);
  const options = reasons[target] ?? reasons.available;
  async function apply() {
    setSaving(true);
    try { await onChange(target, reason); } finally { setSaving(false); }
  }
  return (
    <section className={styles.stateBar} aria-label="Agent state">
      <div><strong>{state.replaceAll("_", " ")}</strong><small className={styles.muted}> {reasonCode ?? "no reason recorded"}{stateChangedAt ? ` · since ${new Date(stateChangedAt).toLocaleTimeString()}` : ""}</small></div>
      <label>Next state<select value={target} onChange={(event) => {
        const next = event.target.value as AgentState;
        setTarget(next);
        setReason((reasons[next] ?? reasons.available)[0].value);
      }}>{Object.keys(reasons).map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
      <label>Reason<select value={reason} onChange={(event) => setReason(event.target.value as StateReasonCode)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <button className={styles.button} disabled={busy || saving} onClick={() => void apply()}>Apply state</button>
      <span className={styles.occupancy}>1h occupancy: {occupancy?.occupancy_percent ? `${Number(occupancy.occupancy_percent).toFixed(1)}%` : "no staffed time"}</span>
    </section>
  );
}
