"use client";

import { useEffect, useState } from "react";
import { api, ApiError, EvidenceRecord } from "@/lib/api";

export default function EvidencePage() {
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.evidenceChain({ limit: 100 })
      .then((rows) => { if (!cancelled) setEvidence(rows); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load evidence"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div className="loading">Loading evidence chain…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  return (
    <>
      <header className="page-head">
        <h1>Evidence Center — Immutable Chain (P0-05)</h1>
        <p className="muted">Backend: app/governance/evidence.py — append_event, verify_event_integrity, verify_chain, hash chain with previous_hash</p>
      </header>
      <section className="card">
        <h2>Chain ({evidence.length} events)</h2>
        {evidence.length === 0 ? <p className="muted">No evidence yet. Trigger governed actions to create chain entries.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Event</th><th>Hash</th><th>Prev</th><th>Time</th></tr></thead>
              <tbody>
                {evidence.map((e) => (
                  <tr key={e.id}><td>{e.event_type}</td><td><code>{e.payload_hash.slice(0,20)}…</code></td><td><code>{e.previous_hash?.slice(0,20) ?? "genesis"}</code></td><td>{new Date(e.created_at).toLocaleString()}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <h2>Verification</h2>
        <ul>
          <li>Hash: SHA256 canonical_json per app/governance/hashing.py</li>
          <li>Chain integrity: verify_chain checks previous_hash linking</li>
          <li>Evidence package: app/governance/attestation.py generate_evidence_package</li>
          <li>Attestation: issue_attestation, verify_attestation with factual claims check</li>
        </ul>
      </section>
    </>
  );
}
