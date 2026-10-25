"use client";

import type { AssistAlert, ComplianceAdherence, ComplianceScript } from "../types";
import styles from "../agent-desktop.module.css";

export function CompliancePromptBar({
  scripts,
  adherence,
  alerts,
}: {
  scripts: ComplianceScript[];
  adherence: ComplianceAdherence[];
  alerts: AssistAlert[];
}) {
  const complianceAlerts = alerts.filter((alert) => alert.kind === "compliance_review");
  return (
    <section className={`${styles.panel} ${complianceAlerts.length ? styles.alert : ""}`} aria-labelledby="compliance-heading">
      <h2 id="compliance-heading">Compliance prompts</h2>
      {complianceAlerts.map((alert) => <p key={alert.id} className={styles.alertText}>{alert.message}</p>)}
      {scripts.length === 0 ? <p className={styles.muted}>No active compliance scripts are configured.</p> : scripts.map((script) => {
        const current = adherence.find((row) => row.script_id === script.id);
        const matched = new Set(current?.matched_phrases ?? []);
        return (
          <details key={script.id} className={styles.complianceScript} open={current?.status === "missed" || current?.status === "violated"}>
            <summary>{script.name} v{script.version} · {script.jurisdiction} · {current?.status ?? "pending"}</summary>
            <p>{script.script_text}</p>
            {script.required_phrases.length > 0 && <ul className={styles.checklist}>
              {script.required_phrases.map((phrase) => <li key={phrase}>
                <span aria-hidden="true">{matched.has(phrase) ? "✓" : "○"}</span> {phrase}
              </li>)}
            </ul>}
            {current && current.violated_phrases.length > 0 && <p className={styles.alertText}>Forbidden phrase detected: {current.violated_phrases.join(", ")}</p>}
          </details>
        );
      })}
    </section>
  );
}
