"use client";

import { canControl, canJoin, statusLabel } from "../lib";
import type { SoftphoneCall } from "../types";
import styles from "../agent-desktop.module.css";

type Props = {
  calls: SoftphoneCall[];
  selectedId: string | null;
  sessionId: string | null;
  busy: boolean;
  onSelect: (id: string) => void;
  onRefresh: () => void;
  onJoin: (call: SoftphoneCall) => void;
  onControl: (call: SoftphoneCall, action: "mute" | "hold", enabled: boolean) => void;
  onEnd: (call: SoftphoneCall) => void;
};

export function CallList({ calls, selectedId, sessionId, busy, onSelect, onRefresh, onJoin, onControl, onEnd }: Props) {
  return <section className={styles.card}>
    <div className={styles.sectionHeader}><h2>Calls</h2><button type="button" className={styles.secondary} onClick={onRefresh} disabled={!sessionId || busy}>Refresh</button></div>
    {!calls.length && <p className={styles.empty}>No softphone calls in this registration.</p>}
    <div className={styles.callList}>
      {calls.map((call) => <div className={call.id === selectedId ? styles.selectedCall : styles.call} key={call.id}>
        <button type="button" className={styles.callSelect} onClick={() => onSelect(call.id)}>
          <strong>{statusLabel(call)}</strong>
          <span>{call.origin.replaceAll("_", " ")} · {call.id.slice(0, 8)}</span>
          <small>{(call.legs ?? []).map((leg) => `${leg.kind}: ${leg.destination_redacted || leg.status}`).join(" · ") || "No provider legs yet"}</small>
        </button>
        <div className={styles.actions}>
          {canJoin(call, sessionId) && <button type="button" onClick={() => onJoin(call)} disabled={busy}>Join</button>}
          {canControl(call) && <>
            <button type="button" className={styles.secondary} onClick={() => onControl(call, "mute", !call.muted)} disabled={busy}>{call.muted ? "Unmute" : "Mute"}</button>
            <button type="button" className={styles.secondary} onClick={() => onControl(call, "hold", !call.on_hold)} disabled={busy}>{call.on_hold ? "Resume" : "Hold"}</button>
          </>}
          {["pending", "dialing", "waiting_for_agent", "in_progress", "held"].includes(call.status) && <button type="button" className={styles.danger} onClick={() => onEnd(call)} disabled={busy}>End</button>}
        </div>
        {call.failure_category && <small className={styles.failure}>Reconciliation: {call.failure_category.replaceAll("_", " ")}</small>}
      </div>)}
    </div>
  </section>;
}
