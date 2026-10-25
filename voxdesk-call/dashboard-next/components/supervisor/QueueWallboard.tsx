"use client";

import type { WallboardQueue } from "../../app/agent-desktop/types";
import styles from "../../app/agent-desktop/agent-desktop.module.css";

export function QueueWallboard({ queues }: { queues: WallboardQueue[] }) {
  return <section className={styles.card} aria-label="Queue wallboard"><h2>Queues</h2><div style={{ overflowX: "auto" }}><table><thead><tr><th>Queue</th><th>Waiting</th><th>Assigned</th><th>Oldest wait</th><th>Estimated wait</th><th>Priority</th></tr></thead><tbody>{queues.map((queue) => <tr key={queue.queue_id}><td>{queue.name}</td><td>{queue.waiting}</td><td>{queue.assigned}</td><td>{queue.oldest_wait_seconds}s</td><td>{queue.estimated_wait_seconds == null ? "Unavailable" : `${queue.estimated_wait_seconds}s`}</td><td>{queue.priority ?? 0}</td></tr>)}</tbody></table></div>{!queues.length && <p className={styles.empty}>No queues are configured for this tenant.</p>}</section>;
}
