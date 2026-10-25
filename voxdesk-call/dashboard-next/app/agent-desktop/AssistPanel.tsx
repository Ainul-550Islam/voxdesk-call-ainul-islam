"use client";

import type { AssistFeedback } from "./hooks/useAssistStream";
import type { AssistAlert, AssistSuggestion, AssistTurn, ComplianceAdherence, ComplianceScript, SoftphoneCall } from "./types";
import styles from "./agent-desktop.module.css";
import { CompliancePromptBar } from "./components/CompliancePromptBar";
import { LiveTranscriptPanel } from "./components/LiveTranscriptPanel";
import { SuggestionPanel } from "./components/SuggestionPanel";

type Props = {
  call: SoftphoneCall;
  assistCallId: string | null;
  transcript?: AssistTurn[];
  suggestions: AssistSuggestion[];
  alerts: AssistAlert[];
  scripts?: ComplianceScript[];
  adherence?: ComplianceAdherence[];
  transport?: "connecting" | "realtime" | "polling";
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onFeedback: (suggestion: AssistSuggestion, value: AssistFeedback) => Promise<boolean>;
  onWrapup: () => void;
};

export function AssistPanel({ call, assistCallId, transcript = [], suggestions, alerts, scripts = [], adherence = [], transport = "polling", busy, onStart, onStop, onFeedback, onWrapup }: Props) {
  return <article className={styles.card}>
    <div className={styles.sectionHeader}>
      <h2>Live agent assist</h2>
      <div className={styles.actions}>
        <button type="button" className={styles.secondary} onClick={onWrapup} disabled={busy || !call.call_id}>Wrap up</button>
        {assistCallId ? <button type="button" className={styles.secondary} onClick={onStop} disabled={busy}>Stop</button> : <button type="button" onClick={onStart} disabled={busy || !call.call_id}>Start</button>}
      </div>
    </div>
    <p className={styles.hint}>Final turns are redacted. Suggestions require retrieved evidence; sentiment is a lexical proxy, not an AI diagnosis. {assistCallId && (transport === "realtime" ? "Realtime stream connected." : transport === "connecting" ? "Connecting realtime; polling fallback active." : "Polling fallback active.")}</p>
    <LiveTranscriptPanel transcript={transcript} transport={transport} />
    {alerts.filter((alert) => alert.kind !== "compliance_review").map((alert) => <div className={styles.alert} key={alert.id}>{alert.message}</div>)}
    <CompliancePromptBar scripts={scripts} adherence={adherence} alerts={alerts} />
    <SuggestionPanel suggestions={suggestions} onFeedback={onFeedback} />
  </article>;
}
