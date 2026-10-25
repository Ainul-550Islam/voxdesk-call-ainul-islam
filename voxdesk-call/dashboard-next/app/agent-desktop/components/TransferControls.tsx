"use client";

import type { SoftphoneCall } from "../types";
import { canControl } from "../lib";
import styles from "../agent-desktop.module.css";

type Props = {
  call: SoftphoneCall;
  destination: string;
  busy: boolean;
  onDestinationChange: (value: string) => void;
  onTransfer: (mode: "blind" | "warm") => void;
  onFinish: (action: "complete" | "cancel") => void;
};

export function TransferControls({ call, destination, busy, onDestinationChange, onTransfer, onFinish }: Props) {
  return <>
    <label>Transfer destination
      <input aria-label="Transfer destination" value={destination} onChange={(event) => onDestinationChange(event.target.value)} placeholder="+15557654321" disabled={busy} />
    </label>
    <div className={styles.actions}>
      <button type="button" onClick={() => onTransfer("warm")} disabled={busy || !canControl(call)}>Warm consult</button>
      <button type="button" className={styles.secondary} onClick={() => onTransfer("blind")} disabled={busy || !canControl(call)}>Blind transfer</button>
      <button type="button" className={styles.secondary} onClick={() => onFinish("complete")} disabled={busy}>Complete warm</button>
      <button type="button" className={styles.secondary} onClick={() => onFinish("cancel")} disabled={busy}>Cancel consult</button>
    </div>
  </>;
}
