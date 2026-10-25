"use client";

import type { AssistTurn } from "../types";
import styles from "../agent-desktop.module.css";

export function LiveTranscriptPanel({
  transcript,
  transport = "polling",
}: {
  transcript: AssistTurn[];
  transport?: "connecting" | "realtime" | "polling";
}) {
  return (
    <section className={styles.panel} aria-labelledby="live-transcript-heading">
      <div className={styles.rowBetween}>
        <h2 id="live-transcript-heading">Live transcript</h2>
        <span className={styles.muted}>{transport === "realtime" ? "Realtime" : transport === "connecting" ? "Connecting" : "Polling fallback"}</span>
      </div>
      <div className={styles.transcript} aria-live="polite">
        {transcript.length === 0 ? <p className={styles.muted}>No transcript turns yet.</p> : transcript.map((turn) => (
          <div key={turn.id} className={styles.transcriptLine}>
            <span className={styles.speaker}>{turn.speaker}</span>
            <span>{turn.text_redacted}</span>
            <small className={styles.muted}>#{turn.seq}</small>
          </div>
        ))}
      </div>
    </section>
  );
}
