"use client";

import styles from "../agent-desktop.module.css";

type Props = { value: string; disabled?: boolean; onChange: (value: string) => void; onSend: () => void };

export function DtmfPad({ value, disabled = false, onChange, onSend }: Props) {
  return <>
    <label>DTMF sequence
      <div className={styles.inline}>
        <input aria-label="DTMF sequence" value={value} onChange={(event) => onChange(event.target.value)} maxLength={32} disabled={disabled} />
        <button type="button" onClick={onSend} disabled={disabled}>Send</button>
      </div>
    </label>
    <div className={styles.dialpad}>
      {["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"].map((digit) => <button type="button" key={digit} className={styles.secondary} onClick={() => onChange(`${value}${digit}`.slice(0, 32))} disabled={disabled}>{digit}</button>)}
    </div>
  </>;
}
