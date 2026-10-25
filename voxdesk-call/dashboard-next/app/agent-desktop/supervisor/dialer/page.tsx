"use client";

import Link from "next/link";
import { useState } from "react";
import { api, errorMessage, requestKey } from "../../lib/api";
import styles from "../../agent-desktop.module.css";

type Mode = "preview" | "progressive" | "predictive" | "ai_agent_blast";
interface Policy {
  id: string; campaign_id: string; mode: Mode; status: string;
  pacing_ratio: string; abandonment_cap: string; max_inflight: number;
  prepared_lead_count: number; jurisdiction: string; consent_required: boolean;
}
interface Precheck { allowed: boolean; reasons: Array<{ code: string; message: string; lead_id?: string }>; evidence: Record<string, unknown> }
interface Measurement { human_answered: number; abandoned: number; rate_percent: string; cap_percent: string; sample_sufficient: boolean; breached: boolean; remaining: number }
interface PacingSnapshot { id: string; mode: string; dial_ratio: string; projected_abandon_rate: string; available_agents: number; reason: string; created_at: string }

export default function DialerConsolePage() {
  const [policyId, setPolicyId] = useState("");
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [precheck, setPrecheck] = useState<Precheck | null>(null);
  const [measurement, setMeasurement] = useState<Measurement | null>(null);
  const [snapshots, setSnapshots] = useState<PacingSnapshot[]>([]);
  const [mode, setMode] = useState<Mode>("preview");
  const [ratio, setRatio] = useState("1");
  const [capPercent, setCapPercent] = useState("3");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load(id = policyId) {
    if (!id.trim()) { setError("Enter a dialer policy UUID."); return; }
    setBusy(true); setError("");
    try {
      const [policyResponse, precheckResponse, pacingResponse, abandonmentResponse] = await Promise.all([
        api<{ policy: Policy }>(`/api/cc2/dialer/policies/${id}`),
        api<Precheck>(`/api/cc2/dialer/policies/${id}/precheck`),
        api<{ snapshots: PacingSnapshot[] }>(`/api/cc2/dialer/policies/${id}/pacing?limit=10`),
        api<{ measurement: Measurement }>(`/api/cc2/dialer/policies/${id}/abandonment`),
      ]);
      setPolicy(policyResponse.policy);
      setMode(policyResponse.policy.mode);
      setRatio(policyResponse.policy.pacing_ratio);
      setCapPercent(String(Number(policyResponse.policy.abandonment_cap) * 100));
      setPrecheck(precheckResponse);
      setSnapshots(pacingResponse.snapshots);
      setMeasurement(abandonmentResponse.measurement);
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }

  async function save() {
    if (!policy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api(`/api/cc2/dialer/policies/${policy.id}`, {
        method: "PATCH",
        body: { mode, pacing_ratio: ratio, abandonment_cap: String(Number(capPercent) / 100) },
      });
      setNotice("Policy configuration saved. Start still requires all server-side compliance checks.");
      await load(policy.id);
    } catch (cause) { setError(errorMessage(cause)); setBusy(false); }
  }

  async function action(name: "start" | "pause" | "stop" | "prepare") {
    if (!policy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const options = {
        method: "POST" as const,
        ...(name === "prepare"
          ? { idempotencyKey: requestKey() }
          : name === "start" ? {} : { body: { reason: `Supervisor console ${name}` } }),
      };
      await api(`/api/cc2/dialer/policies/${policy.id}/${name}`, options);
      setNotice(`Dialer ${name} request accepted; refreshed status is shown below.`);
      await load(policy.id);
    } catch (cause) { setError(errorMessage(cause)); setBusy(false); }
  }

  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.eyebrow}>SUPERVISOR CONSOLE</p><h1>Dialer controls</h1><p>Preview, progressive, predictive, and AI-agent blasting with server-enforced compliance.</p></div><div><Link href="/agent-desktop/supervisor/wallboard">Wallboard</Link> · <Link href="/agent-desktop">Agent desktop</Link></div></header>
    {error && <p className={styles.error} role="alert">{error}</p>}{notice && <p className={styles.notice}>{notice}</p>}
    <section className={styles.card}><h2>Load policy</h2><div className={styles.inlineForm}><label>Policy UUID<input value={policyId} onChange={(event) => setPolicyId(event.target.value)} placeholder="Existing dialer policy UUID" /></label><button className={styles.button} disabled={busy} onClick={() => void load()}>Load</button></div></section>
    {policy && <>
      <section className={styles.card}><div className={styles.rowBetween}><h2>{policy.mode.replaceAll("_", " ")} · {policy.status}</h2><span className={styles.muted}>{policy.id}</span></div>
        <p>Campaign {policy.campaign_id} · Jurisdiction {policy.jurisdiction} · Prepared contacts {policy.prepared_lead_count} · Consent required {policy.consent_required ? "yes" : "no"}</p>
        <div className={styles.statGrid}><div className={styles.stat}><strong>{measurement?.human_answered ?? 0}</strong><span>Human answered (30d)</span></div><div className={styles.stat}><strong>{measurement?.abandoned ?? 0}</strong><span>Abandoned (30d)</span></div><div className={styles.stat}><strong>{measurement ? `${Number(measurement.rate_percent).toFixed(2)}%` : "—"}</strong><span>Abandon rate / cap {measurement?.cap_percent ?? "—"}%</span></div></div>
        <div className={styles.buttonRow}><button className={styles.buttonSecondary} disabled={busy} onClick={() => void action("prepare")}>Prepare evidence</button><button className={styles.button} disabled={busy || policy.status === "running"} onClick={() => void action("start")}>Start</button><button className={styles.buttonSecondary} disabled={busy || policy.status !== "running"} onClick={() => void action("pause")}>Pause</button><button className={styles.buttonDanger} disabled={busy || policy.status === "stopped"} onClick={() => void action("stop")}>Stop</button></div>
      </section>
      <section className={styles.card}><h2>Pacing configuration</h2><div className={styles.grid}>
        <label>Mode<select value={mode} disabled={busy || policy.status === "running"} onChange={(event) => setMode(event.target.value as Mode)}><option value="preview">Preview</option><option value="progressive">Progressive</option><option value="predictive">Predictive</option><option value="ai_agent_blast">AI agent blast</option></select></label>
        <label>Pacing ratio<input type="number" min={mode === "ai_agent_blast" ? 1 : 0.001} step={mode === "ai_agent_blast" ? 1 : 0.1} value={ratio} onChange={(event) => setRatio(event.target.value)} /></label>
        <label>Abandon cap (%)<input type="number" min="0.01" max="10" step="0.1" value={capPercent} onChange={(event) => setCapPercent(event.target.value)} /></label>
      </div><p className={styles.hint}>AI pacing is a whole number of new attempts per tick. Caps above 3% also require a valid audited jurisdiction override; the server fails closed.</p><button className={styles.button} disabled={busy || policy.status === "running"} onClick={() => void save()}>Save configuration</button></section>
      <section className={styles.panel}><h2>Compliance precheck</h2><p className={precheck?.allowed ? styles.notice : styles.alertText}>{precheck?.allowed ? "Allowed by the latest server precheck" : "Blocked by the latest server precheck"}</p>{precheck?.reasons.map((reason, index) => <p key={`${reason.code}-${index}`} className={styles.alertText}>{reason.code}: {reason.message}</p>)}</section>
      <section className={styles.panel}><h2>Recent pacing decisions</h2>{snapshots.length === 0 ? <p className={styles.muted}>No pacing snapshots have been recorded.</p> : <table className={styles.table}><thead><tr><th>Time</th><th>Mode</th><th>Ratio</th><th>Projected abandon</th><th>Available agents</th><th>Reason</th></tr></thead><tbody>{snapshots.map((row) => <tr key={row.id}><td>{new Date(row.created_at).toLocaleTimeString()}</td><td>{row.mode}</td><td>{row.dial_ratio}</td><td>{(Number(row.projected_abandon_rate) * 100).toFixed(2)}%</td><td>{row.available_agents}</td><td>{row.reason}</td></tr>)}</tbody></table>}</section>
    </>}
  </main>;
}
