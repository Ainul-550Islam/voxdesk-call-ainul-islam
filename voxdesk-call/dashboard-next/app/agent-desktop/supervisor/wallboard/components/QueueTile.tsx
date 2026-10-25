import type { WallboardQueue } from "../../../types";
import styles from "../../../agent-desktop.module.css";

function value(metric: number | null | undefined, suffix = "") {
  return metric === null || metric === undefined ? "—" : `${metric.toFixed(metric % 1 ? 1 : 0)}${suffix}`;
}

export function QueueTile({ queue }: { queue: WallboardQueue }) {
  return <article className={styles.panel}>
    <div className={styles.rowBetween}><h2>{queue.name}</h2><span className={queue.enabled ? styles.online : styles.offline}>{queue.enabled ? "Enabled" : "Disabled"}</span></div>
    <div className={styles.statGrid}>
      <div className={styles.stat}><strong>{queue.waiting}</strong><span>Waiting</span></div>
      <div className={styles.stat}><strong>{queue.assigned}</strong><span>Assigned</span></div>
      <div className={styles.stat}><strong>{value(queue.estimated_wait_seconds, "s")}</strong><span>EWT</span></div>
      <div className={styles.stat}><strong>{value(queue.service_level_percent, "%")}</strong><span>Service level</span></div>
      <div className={styles.stat}><strong>{value(queue.asa_seconds, "s")}</strong><span>ASA</span></div>
      <div className={styles.stat}><strong>{value(queue.aht_seconds, "s")}</strong><span>AHT</span></div>
      <div className={styles.stat}><strong>{queue.offered ?? 0}</strong><span>Offered (30d)</span></div>
      <div className={styles.stat}><strong>{queue.abandoned ?? 0}</strong><span>Abandoned (30d)</span></div>
      <div className={styles.stat}><strong>{value(queue.abandon_percent, "%")}</strong><span>Abandon rate</span></div>
    </div>
    <p className={styles.muted}>Oldest wait {queue.oldest_wait_seconds}s · Priority {queue.priority ?? 0} · {queue.estimate_method ?? "EWT method unavailable"}</p>
  </article>;
}
