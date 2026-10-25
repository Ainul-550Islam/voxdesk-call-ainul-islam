"use client";

import { useState } from "react";
import { citationLabel } from "../lib";
import type { AssistFeedback } from "../hooks/useAssistStream";
import type { AssistSuggestion } from "../types";
import styles from "../agent-desktop.module.css";

export function SuggestionPanel({
  suggestions,
  onFeedback,
}: {
  suggestions: AssistSuggestion[];
  onFeedback: (suggestion: AssistSuggestion, value: AssistFeedback) => Promise<boolean>;
}) {
  const [busyId, setBusyId] = useState("");
  async function send(suggestion: AssistSuggestion, value: AssistFeedback) {
    setBusyId(suggestion.id);
    try { await onFeedback(suggestion, value); } finally { setBusyId(""); }
  }
  return (
    <section className={styles.panel} aria-labelledby="suggestion-heading">
      <h2 id="suggestion-heading">Next best actions</h2>
      {suggestions.length === 0 ? <p className={styles.muted}>No grounded suggestions yet.</p> : suggestions.map((suggestion) => (
        <article key={suggestion.id} className={styles.suggestion}>
          <div className={styles.rowBetween}>
            <strong>{suggestion.kind === "knowledge" ? "Knowledge" : "Next best action"}</strong>
            <span className={styles.muted}>{suggestion.generation_method}</span>
          </div>
          <p>{suggestion.text}</p>
          {suggestion.citations.length > 0 && <ul className={styles.citations}>
            {suggestion.citations.map((citation, index) => {
              const source = citation.source_id ?? citation.document_id;
              const snippet = typeof citation.snippet === "string" ? citation.snippet : "";
              return <li key={String(source ?? index)}>
                {source ? <a href={`/knowledge-base/${encodeURIComponent(String(source))}`}>{citationLabel(citation, index)}</a> : <span>{citationLabel(citation, index)}</span>}
                {snippet && <span className={styles.muted}> — {snippet}</span>}
              </li>;
            })}
          </ul>}
          <div className={styles.buttonRow}>
            <button className={styles.buttonSecondary} disabled={busyId === suggestion.id} onClick={() => void send(suggestion, "accepted")}>Accept</button>
            <button className={styles.buttonSecondary} disabled={busyId === suggestion.id} onClick={() => void send(suggestion, "dismissed")}>Ignore</button>
            {suggestion.feedback && <span className={styles.muted}>Feedback: {suggestion.feedback}</span>}
          </div>
        </article>
      ))}
    </section>
  );
}
