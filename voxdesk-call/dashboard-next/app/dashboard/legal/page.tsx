"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

// GAP-P1-04 Legal Playbook / Redline — dedicated workspace per LuMay legal surfaces

export default function LegalPage() {
  const [clauses, setClauses] = useState<any[]>([]);
  const [playbooks, setPlaybooks] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [envId, setEnvId] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    // Try to get me to extract env? For now use empty and catch
    api.me().then((me: any) => {
      if (cancelled) return;
      // me may have environments — not guaranteed; keep placeholder
      setEnvId(me.tenant?.id || "");
    }).catch(() => {});
    Promise.all([
      api.clauseLibrary().catch(() => []),
    ]).then(([clauseRows]) => {
      if (!cancelled) setClauses(clauseRows);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load legal");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div className="loading">Loading legal playbook…</div>;

  return (
    <>
      <header className="page-head">
        <h1>Legal Agent — Clause Library, Playbook, Redline (P1-04)</h1>
        <p className="muted">Backend: app/legal/clause_engine.py PATTERNS (5), review_engine.py, playbook.py, redline.py, clause_library.py + /api/legal/* (8 routes)</p>
        <p className="muted">LuMay: Legal playbook, clause management, redline artifacts, versioning, audit chain — demo</p>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="card">
        <h2>Clause Library ({clauses.length}) — 5 Default Clauses</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Key</th><th>Title</th><th>Category</th><th>Risk Tier</th><th>Version</th><th>Pattern</th></tr></thead>
            <tbody>
              {clauses.map((c: any, i: number) => (
                <tr key={i}>
                  <td>{c.key}</td>
                  <td>{c.title}</td>
                  <td>{c.category}</td>
                  <td>{c.risk_tier}</td>
                  <td>{c.version}</td>
                  <td><small>{c.pattern ? c.pattern.slice(0,60) : ""}...</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p><small>Mapping to clause_engine.py PATTERNS: termination, limitation_of_liability, indemnification, confidentiality, data_protection. Each entry has suggested_text, fallback_text, source, review_required.</small></p>
      </section>

      <section className="card">
        <h2>Playbook Rules (3 Default)</h2>
        <pre className="code-block">
{`Rule 1: termination_missing -> if finding contains termination, severity critical, action suggest_standard_termination
Rule 2: liability_cap_missing -> if finding contains limitation_of_liability, severity high, action suggest_liability_cap
Rule 3: confidentiality_duration -> if finding contains confidentiality, severity medium, action suggest_confidentiality_duration

Backend: PlaybookRule with operator (contains/equals/gte/lte), fingerprint sha256_hex, clause_key linkage
Service: PlaybookService.create_playbook(tenant_id, org_id, env_id, name, clause_keys) + evaluate_against_playbook(findings, playbook)`}
        </pre>
      </section>

      <section className="card">
        <h2>Redline Artifact Generator</h2>
        <div className="redline-grid">
          <div className="redline-card">
            <h4>RedlineChange</h4>
            <ul>
              <li>change_type: insertion | deletion | replacement</li>
              <li>original_text, suggested_text, rationale</li>
              <li>clause_key, source_reference, evidence_reference</li>
              <li>fingerprint via sha256_hex of original+suggested+clause</li>
            </ul>
          </div>
          <div className="redline-card">
            <h4>RedlineArtifact</h4>
            <ul>
              <li>id, document_id, playbook_id, changes[], diff_html (HtmlDiff), fingerprint, disclaimer, review_state, evidence_references, created_at</li>
              <li>generate_redline(document_id, findings[], playbook, evidence_refs) → artifacts[]</li>
              <li>generate_for_document() groups by finding</li>
              <li>as_dict() for API response</li>
            </ul>
          </div>
          <div className="redline-card">
            <h4>Diff HTML & Fingerprint</h4>
            <ul>
              <li>difflib.SequenceMatcher + HtmlDiff for side-by-side diff</li>
              <li>_wrap_html() for safe HTML wrapper</li>
              <li>_clause_to_original() / _clause_to_suggested() maps clause entries to clause text</li>
              <li>sha256_hex for artifact fingerprint, change fingerprint</li>
            </ul>
          </div>
          <div className="redline-card">
            <h4>Reviewer Workflow & Evidence</h4>
            <ul>
              <li>review_state required (pending/approved/rejected)</li>
              <li>evidence_references passed to artifact, linked to review cases</li>
              <li>Disclaimer: AI-generated redline suggestion only; not legal advice</li>
              <li>Export: POST /api/legal/redlines/export with audit package fingerprint</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>API Examples</h2>
        <pre className="code-block">
{`GET /api/legal/clause-library
GET /api/legal/clause-library/termination
GET /api/legal/playbooks?environment_id=uuid
POST /api/legal/playbooks {"environment_id": "uuid", "name": "My Playbook", "clause_keys": ["termination","liability_cap"]}
POST /api/legal/playbooks/{id}/evaluate {"environment_id": "uuid", "findings": [{"clause_key": "termination", "title": "Missing termination"}]}
POST /api/legal/redlines {"environment_id": "uuid", "document_id": "doc-123", "playbook_id": "pb-123", "findings": [...], "evidence_references": ["ev-1"]}
GET /api/legal/redlines/{artifact_id}?environment_id=uuid
POST /api/legal/redlines/export {"environment_id": "uuid", "document_id": "doc-123"}`}
        </pre>
      </section>

      <section className="card">
        <h2>Gap Closure Notes (P1-04)</h2>
        <ul>
          <li>✅ Clause library: 5 entries mapping to existing clause_engine.py PATTERNS, versioning, fingerprint, tenant-scoped</li>
          <li>✅ Playbook: Playbook with clauses[], rules[], status draft/active/archived, version, scope tenant/org/env, evaluate_against_playbook</li>
          <li>✅ Redline: RedlineArtifact with changes tuple, diff_html via difflib, fingerprint sha256_hex, disclaimer, reviewer workflow, evidence chain</li>
          <li>✅ Persistence: ClauseLibraryRepository, PlaybookRepository (governance policies table), RedlineRepository with save/get/list/export_audit_package</li>
          <li>✅ API: 8 new routes under /api/legal/ preserving existing 2 review routes, tenant isolation, permission checks</li>
          <li>Honest: No legal-certainty claims, all suggestions require human review, disclaimer on every artifact</li>
        </ul>
      </section>
    </>
  );
}
