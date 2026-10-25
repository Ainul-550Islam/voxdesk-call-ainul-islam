"use client";

import Link from "next/link";
import { useWallboard } from "../../hooks/useWallboard";
import styles from "../../agent-desktop.module.css";
import { AgentGrid } from "./components/AgentGrid";
import { QueueTile } from "./components/QueueTile";

export default function SupervisorWallboardPage() {
  const { snapshot, loading, error, transport, reload } = useWallboard(5000);
  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.eyebrow}>VOXDESK CONTACT CENTER</p><h1>Supervisor wallboard</h1><p>Durable queue statistics with a one-second live snapshot cache.</p></div><div><Link href="/agent-desktop">Agent desktop</Link> · <Link href="/agent-desktop/supervisor/dialer">Dialer console</Link></div></header>
    {error && <p className={styles.error} role="alert">{error}</p>}
    <div className={styles.stateBar}><span>Transport: {transport}</span><span>{snapshot ? `Generated ${new Date(snapshot.generated_at).toLocaleTimeString()}` : loading ? "Loading wallboard…" : "No snapshot received"}</span><button className={styles.buttonSecondary} onClick={() => void reload()}>Refresh</button></div>
    {snapshot && <>
      <div className={styles.grid}>{snapshot.queues.map((queue) => <QueueTile key={queue.queue_id} queue={queue} />)}</div>
      {!snapshot.queues.length && <p className={styles.empty}>No queues are configured for this tenant.</p>}
      <AgentGrid agents={snapshot.agents} />
      <section className={styles.panel}><h2>Operational totals</h2><p>Scheduled callbacks: {snapshot.callbacks.scheduled ?? 0} · Active assist sessions: {snapshot.assist.active_sessions} · Open assist alerts: {snapshot.assist.open_alerts}</p></section>
    </>}
  </main>;
}
