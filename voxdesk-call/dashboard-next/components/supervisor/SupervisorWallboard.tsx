"use client";

import type { WallboardSnapshot } from "../../app/agent-desktop/types";
import styles from "../../app/agent-desktop/agent-desktop.module.css";
import { AgentRoster } from "./AgentRoster";
import { QueueWallboard } from "./QueueWallboard";

type Props = {
  snapshot: WallboardSnapshot | null;
  loading: boolean;
  error?: string;
  transport?: "connecting" | "realtime" | "polling";
  onRefresh?: () => void;
};

export function SupervisorWallboard({ snapshot, loading, error = "", transport = "polling", onRefresh }: Props) {
  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.eyebrow}>CONTACT CENTER · SUPERVISOR</p><h1>Live wallboard</h1><p>Queue depth, agent occupancy inputs, callbacks, and assist alerts from durable rows.</p></div>{onRefresh && <button type="button" onClick={onRefresh} disabled={loading}>Refresh</button>}</header>
    {error && <div className={styles.error} role="alert">{error}</div>}
    {loading && !snapshot && <p>Loading wallboard…</p>}
    {snapshot && <>
      <p className={styles.hint}>Generated {new Date(snapshot.generated_at).toLocaleString()} · {transport === "realtime" ? "Realtime stream" : transport === "connecting" ? "Connecting realtime; polling fallback" : "Polling fallback"}</p>
      <QueueWallboard queues={snapshot.queues} />
      <AgentRoster agents={snapshot.agents} />
      <section className={styles.grid}><article className={styles.card}><h2>Callbacks</h2>{Object.entries(snapshot.callbacks).map(([status, count]) => <p key={status}>{status}: {count}</p>)}</article><article className={styles.card}><h2>Assist</h2><p>Active sessions: {snapshot.assist.active_sessions}</p><p>Open alerts: {snapshot.assist.open_alerts}</p></article></section>
    </>}
  </main>;
}
