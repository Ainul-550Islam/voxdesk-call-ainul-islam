"use client";

import type { SoftphoneSession } from "./types";
import styles from "./agent-desktop.module.css";

type Props = {
  environmentId: string;
  deviceLabel: string;
  session: SoftphoneSession | null;
  sdkReady: boolean;
  busy: boolean;
  onEnvironmentChange: (value: string) => void;
  onDeviceLabelChange: (value: string) => void;
  onRegister: () => void;
  onPresence: (state: "available" | "away" | "offline") => void;
};

export function Softphone({ environmentId, deviceLabel, session, sdkReady, busy, onEnvironmentChange, onDeviceLabelChange, onRegister, onPresence }: Props) {
  return <article className={styles.card}>
    <h2>1 · Register</h2>
    <label>Production environment UUID
      <input aria-label="Production environment UUID" value={environmentId} onChange={(event) => onEnvironmentChange(event.target.value)} disabled={busy || Boolean(session)} />
    </label>
    <label>Device label
      <input aria-label="Device label" value={deviceLabel} onChange={(event) => onDeviceLabelChange(event.target.value)} disabled={busy || Boolean(session)} maxLength={100} />
    </label>
    <button type="button" onClick={onRegister} disabled={busy || !sdkReady || Boolean(session)}>Register softphone</button>
    <div className={styles.presence}>
      <span>Agent state</span>
      <button type="button" className={styles.secondary} onClick={() => onPresence("available")} disabled={busy || !session}>Available</button>
      <button type="button" className={styles.secondary} onClick={() => onPresence("away")} disabled={busy || !session}>Away</button>
      <button type="button" className={styles.secondary} onClick={() => onPresence("offline")} disabled={busy || !session}>Offline</button>
    </div>
  </article>;
}
