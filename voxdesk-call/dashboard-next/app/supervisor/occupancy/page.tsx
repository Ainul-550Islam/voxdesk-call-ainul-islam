"use client";

import { useState } from "react";
import { useOccupancy } from "../../agent-desktop/hooks/useOccupancy";
import styles from "../../agent-desktop/agent-desktop.module.css";

function isoHoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3600_000).toISOString();
}

export default function SupervisorOccupancyPage() {
  const [start, setStart] = useState(() => isoHoursAgo(1));
  const [end, setEnd] = useState(() => new Date().toISOString());
  const [userId, setUserId] = useState("");
  const { rows, loading, error, load } = useOccupancy();
  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.eyebrow}>CONTACT CENTER · SUPERVISOR</p><h1>Agent occupancy</h1><p>Intervals are clipped to the requested window; no simulated occupancy is shown.</p></div></header>
    {error && <div className={styles.error} role="alert">{error}</div>}
    <section className={styles.card}>
      <label>Window start (ISO)<input aria-label="Window start" value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <label>Window end (ISO)<input aria-label="Window end" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      <label>Agent UUID (optional)<input aria-label="Agent UUID" value={userId} onChange={(event) => setUserId(event.target.value)} /></label>
      <button type="button" disabled={loading} onClick={() => void load(start, end, userId.trim() || undefined)}>Load occupancy</button>
    </section>
    <section className={styles.card}><h2>Interval accounting</h2>{!rows.length && <p className={styles.empty}>No occupancy rows loaded.</p>}<div style={{ overflowX: "auto" }}><table><thead><tr><th>Agent</th><th>Occupancy</th><th>State seconds</th></tr></thead><tbody>{rows.map((row) => <tr key={row.user_id}><td>{row.user_id}</td><td>{row.occupancy}</td><td>{Object.entries(row.seconds).map(([state, seconds]) => `${state}: ${seconds}`).join(" · ")}</td></tr>)}</tbody></table></div></section>
  </main>;
}
