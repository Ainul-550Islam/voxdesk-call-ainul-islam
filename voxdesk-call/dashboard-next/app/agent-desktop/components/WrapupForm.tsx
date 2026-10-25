"use client";

import { useEffect, useState } from "react";
import type { WrapupDraft } from "../types";
import styles from "../agent-desktop.module.css";

type Props = {
  wrapups: WrapupDraft[];
  busy: boolean;
  onSubmit: (callId: string, disposition: string, summary: string, tags: string[]) => Promise<boolean>;
};

export function WrapupForm({ wrapups, busy, onSubmit }: Props) {
  const current = wrapups[0] ?? null;
  const [disposition, setDisposition] = useState("");
  const [summary, setSummary] = useState("");
  const [tags, setTags] = useState("");
  const [approved, setApproved] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setDisposition(current?.disposition ?? "");
    setSummary(current?.summary_redacted ?? "");
    setTags((current?.tags ?? []).join(", "));
    setApproved(false);
    setNow(Date.now());
  }, [current?.id, current?.disposition, current?.summary_redacted, current?.tags]);
  useEffect(() => {
    if (!current) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [current]);
  if (!current) return null;
  const remainingSeconds = Math.floor((new Date(current.deadline_at).getTime() - now) / 1000);
  const overdue = remainingSeconds < 0;
  const timerLabel = overdue
    ? `Overdue by ${Math.abs(remainingSeconds)}s; submission is still required`
    : `${remainingSeconds}s remaining`;
  return <section className={styles.card} role="dialog" aria-modal="true" aria-labelledby="wrapup-title">
    <h2 id="wrapup-title">After-call wrap-up approval</h2>
    <p className={overdue ? styles.alertText : styles.hint} role="timer">{timerLabel}</p>
    <p className={styles.hint}>Review the redacted summary and disposition. Submission is your approval and queues CRM writeback only when an integration is configured. You cannot return to available with this wrap-up pending.</p>
    <label>Disposition<input aria-label="Wrap-up disposition" value={disposition} onChange={(event) => setDisposition(event.target.value)} maxLength={80} disabled={busy} /></label>
    <label>Redacted summary<textarea aria-label="Redacted summary" value={summary} onChange={(event) => setSummary(event.target.value)} maxLength={8000} disabled={busy} /></label>
    <label>Tags (comma separated)<input aria-label="Wrap-up tags" value={tags} onChange={(event) => setTags(event.target.value)} disabled={busy} /></label>
    <label><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} disabled={busy} /> I approve this redacted wrap-up for submission</label>
    <button type="button" disabled={busy || !approved || !disposition.trim()} onClick={() => void onSubmit(current.call_id, disposition.trim(), summary, tags.split(",").map((tag) => tag.trim()).filter(Boolean))}>Approve &amp; submit wrap-up</button>
  </section>;
}
