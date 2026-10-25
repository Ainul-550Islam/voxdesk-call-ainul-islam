"use client";

import type { SoftphoneCall } from "../types";
import styles from "../agent-desktop.module.css";

export function CustomerContextCard({ call }: { call: SoftphoneCall | null }) {
  if (!call) return <section className={styles.panel}><h2>Customer context</h2><p className={styles.muted}>Select a call to load its linked CRM context.</p></section>;
  const context = call.customer_context;
  const briefing = call.warm_transfer_briefing;
  return (
    <section className={styles.panel} aria-labelledby="customer-context-heading">
      <h2 id="customer-context-heading">Customer context</h2>
      <p><strong>Origin:</strong> {call.origin} · <strong>Status:</strong> {call.status}</p>
      {context?.lead ? <div>
        <h3>{context.lead.name || "Unnamed linked lead"}</h3>
        <p>{context.lead.company || "No company recorded"} · Score {context.lead.score ?? "not set"}</p>
      </div> : <p className={styles.muted}>No linked lead is available for this call.</p>}
      {briefing && <div className={styles.briefing}>
        <h3>AI briefing</h3>
        <p>{briefing.text_redacted || "No redacted briefing text was recorded."}</p>
        <small className={styles.muted}>{briefing.status} · human briefed: {briefing.human_briefed ? "yes" : "no"}</small>
      </div>}
      <h3>Prior calls</h3>
      {!context || context.prior_calls.length === 0 ? <p className={styles.muted}>No prior calls are linked to this lead.</p> : <ul className={styles.history}>
        {context.prior_calls.map((prior) => <li key={prior.id}>
          <strong>{new Date(prior.started_at).toLocaleString()}</strong> · {prior.status} · {prior.duration_seconds}s
          {prior.summary_redacted && <p>{prior.summary_redacted}</p>}
        </li>)}
      </ul>}
      {context && Object.keys(context.crm).length > 0 && <dl className={styles.metaGrid}>
        {Object.entries(context.crm).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}
      </dl>}
    </section>
  );
}
