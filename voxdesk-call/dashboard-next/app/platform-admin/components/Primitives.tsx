"use client";

/**
 * Small presentational pieces shared by the platform-admin pages.
 *
 * They are deliberately dumb: no fetching, no formatting decisions hidden inside a
 * component. Everything that could be wrong in an interesting way lives in
 * `lib/format.ts` where it has a test, and everything that fetches lives in the page.
 */

import type { ReactNode } from "react";

import type { Tone } from "../lib/format";
import styles from "../platform-admin.module.css";

export function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className={`${styles.pill} ${styles[`pill_${tone}`]}`} data-tone={tone}>
      {label}
    </span>
  );
}

export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className={styles.metric}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={styles.metricValue}>{value}</div>
      {hint ? <div className={styles.metricHint}>{hint}</div> : null}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={styles.panel}>
      <header className={styles.panelHeader}>
        <div>
          <h2 className={styles.panelTitle}>{title}</h2>
          {subtitle ? <p className={styles.panelSubtitle}>{subtitle}</p> : null}
        </div>
        {actions ? <div className={styles.panelActions}>{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

/**
 * An honesty note. Used for the things a green tick would otherwise imply: that a push
 * was delivered, that DNS is globally propagated, that a certificate is live.
 */
export function Callout({
  tone = "info",
  role = "note",
  children,
}: {
  tone?: Tone;
  role?: "note" | "alert";
  children: ReactNode;
}) {
  return (
    <p className={`${styles.callout} ${styles[`callout_${tone}`]}`} data-tone={tone} role={role}>
      {children}
    </p>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className={styles.state} role="status">
      Loading {what}…
    </p>
  );
}

export function Failure({ what, error, onRetry }: { what: string; error: Error; onRetry?: () => void }) {
  return (
    <div className={styles.state} role="alert">
      <p>
        Could not load {what}: {error.message}
      </p>
      {onRetry ? (
        <button type="button" className={styles.button} onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className={styles.state}>{children}</p>;
}
