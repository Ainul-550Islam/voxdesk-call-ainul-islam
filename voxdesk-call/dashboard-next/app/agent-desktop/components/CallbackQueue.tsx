"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import type { CallbackRequest } from "../types";
import styles from "../agent-desktop.module.css";

export function CallbackQueue({ onError }: { onError?: (message: string) => void }) {
  const [callbacks, setCallbacks] = useState<CallbackRequest[]>([]);
  const [editing, setEditing] = useState("");
  const [localTime, setLocalTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  const load = useCallback(async () => {
    try {
      const response = await api<{ callbacks: CallbackRequest[] }>("/api/cc2/acd/callbacks?mine=true");
      setCallbacks(response.callbacks.filter((row) => row.status === "scheduled" || row.status === "running"));
      setError("");
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      onError?.(message);
    }
  }, [onError]);
  useEffect(() => { void load(); }, [load]);

  async function cancel(id: string) {
    setBusy(true);
    try {
      await api(`/api/cc2/acd/callbacks/${id}/cancel`, { method: "POST", body: { reason: "Cancelled by assigned agent" } });
      await load();
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function reschedule(id: string) {
    if (!localTime) { setError("Choose a new callback date and time."); return; }
    setBusy(true);
    try {
      await api(`/api/cc2/acd/callbacks/${id}/reschedule`, {
        method: "POST",
        body: { scheduled_at: new Date(localTime).toISOString(), timezone },
      });
      setEditing("");
      setLocalTime("");
      await load();
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }

  return (
    <section className={styles.panel} aria-labelledby="callback-queue-heading">
      <div className={styles.rowBetween}><h2 id="callback-queue-heading">My callbacks</h2><button className={styles.buttonSecondary} onClick={() => void load()} disabled={busy}>Refresh</button></div>
      {error && <p className={styles.alertText} role="alert">{error}</p>}
      {callbacks.length === 0 ? <p className={styles.muted}>No scheduled or queued callbacks are assigned to you.</p> : <ul className={styles.list}>
        {callbacks.map((callback) => <li key={callback.id} className={styles.listItem}>
          <strong>{callback.status}</strong> · {new Date(callback.next_attempt_at).toLocaleString()} · {callback.timezone}
          <small className={styles.muted}> Attempts {callback.attempts} · Queue {callback.queue_id}</small>
          {callback.note && <p>{callback.note}</p>}
          {callback.failure_category && <p className={styles.alertText}>{callback.failure_category}</p>}
          <div className={styles.buttonRow}>
            {callback.status === "scheduled" && <>
              <button className={styles.buttonSecondary} disabled={busy} onClick={() => { setEditing(editing === callback.id ? "" : callback.id); setLocalTime(""); }}>Reschedule</button>
              <button className={styles.buttonDanger} disabled={busy} onClick={() => void cancel(callback.id)}>Cancel</button>
            </>}
          </div>
          {editing === callback.id && <div className={styles.inlineForm}>
            <label>New local time<input type="datetime-local" value={localTime} onChange={(event) => setLocalTime(event.target.value)} /></label>
            <button className={styles.button} disabled={busy} onClick={() => void reschedule(callback.id)}>Save in {timezone}</button>
          </div>}
        </li>)}
      </ul>}
    </section>
  );
}
