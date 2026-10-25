"use client";

import type { SoftphoneSession } from "../types";
import styles from "../agent-desktop.module.css";

type Props = {
  destination: string;
  callerId: string;
  session: SoftphoneSession | null;
  busy: boolean;
  onDestinationChange: (value: string) => void;
  onCallerIdChange: (value: string) => void;
  onDial: () => void;
};

export function OutboundDialer({ destination, callerId, session, busy, onDestinationChange, onCallerIdChange, onDial }: Props) {
  return <article className={styles.card}>
    <h2>2 · Outbound</h2>
    <label>Destination
      <input aria-label="Destination" value={destination} onChange={(event) => onDestinationChange(event.target.value)} placeholder="+15551234567" disabled={busy || !session} />
    </label>
    <label>Verified caller ID (optional)
      <input aria-label="Verified caller ID (optional)" value={callerId} onChange={(event) => onCallerIdChange(event.target.value)} placeholder="Tenant default" disabled={busy || !session} />
    </label>
    <button type="button" onClick={onDial} disabled={busy || !session}>Create customer leg</button>
    <p className={styles.hint}>DNC, tenant caller-ID trust, and current affirmative voice consent are checked before dialing.</p>
  </article>;
}
