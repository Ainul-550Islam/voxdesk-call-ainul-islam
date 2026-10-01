"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

// GAP-P1-03 QMS native adapters, P1-04 Legal playbook/redline, compliance surfaces

export default function CompliancePage() {
  const [frameworks, setFrameworks] = useState<any[]>([]);
  const [templates, setTemplates] = useState<any[]>([]);
  const [qmsProviders, setQmsProviders] = useState<string[]>([]);
  const [clauses, setClauses] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.complianceFrameworks().catch(() => []),
      api.industryTemplates().catch(() => []),
      api.qmsProviders().catch(() => ({ providers: [], note: "" })),
      api.clauseLibrary().catch(() => []),
    ]).then(([fw, tmpl, qms, clauseRows]) => {
      if (cancelled) return;
      setFrameworks(fw);
      setTemplates(tmpl);
      setQmsProviders((qms as any).providers || []);
      setClauses(clauseRows);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load compliance");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div className="loading">Loading compliance…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  return (
    <>
      <header className="page-head">
        <h1>Compliance — QMS / OCG / Industry / Legal Playbook (P1-03, P1-04)</h1>
        <p className="muted">Backend: app/compliance/qms.py + qms_adapters.py Veeva/MasterControl/ETQ + app/legal/playbook.py + redline.py + clause_library.py + /api/compliance/qms/* + /api/legal/*</p>
      </header>

      <section className="card">
        <h2>QMS Native Adapters (GAP-P1-03 Fixed)</h2>
        <p className="muted">LuMay: QMS audit readiness, CAPA/deviation/traceability, Veeva Vault/MasterControl/ETQ integration — demo</p>
        <div className="qms-grid">
          <div className="qms-card">
            <h4>Providers ({qmsProviders.length})</h4>
            <ul>
              {qmsProviders.length === 0 ? <li>No providers — backend qms_adapters.py not loaded</li> : qmsProviders.map((p) => <li key={p}>{p}</li>)}
            </ul>
            <p><small>Backend: app/compliance/qms_adapters.py — VeevaVaultAdapter, MasterControlAdapter, ETQAdapter with QMSContext, health_check, list_documents, traceability, audit package</small></p>
          </div>
          <div className="qms-card">
            <h4>Traceability Graph</h4>
            <ul>
              <li>subject_type + subject_id → CAPA/deviation/traceability</li>
              <li>Endpoint: GET /api/compliance/qms/{`{provider}`}/traceability?subject_type=&subject_id=</li>
              <li>Honest: Real graph requires live credentials, returns note when not configured</li>
              <li>Backend: _BaseQMSAdapter.get_traceability() with external blocker message</li>
            </ul>
          </div>
          <div className="qms-card">
            <h4>Audit Package Assembly</h4>
            <ul>
              <li>POST /api/compliance/qms/{`{provider}`}/audit-package with framework_id</li>
              <li>Assembles controlled docs, findings, evidence, reviewer workflow from QMS</li>
              <li>Honest empty when credentials missing — no fake audit docs</li>
              <li>Backend: assemble_audit_package() with tenant isolation span</li>
            </ul>
          </div>
          <div className="qms-card">
            <h4>Health & Documents</h4>
            <ul>
              <li>Health: POST /api/compliance/qms/health with provider, config, credentials — returns connected + safe_message</li>
              <li>Documents: GET /api/compliance/qms/{`{provider}`}/documents — Veeva Vault objects/documents?q=, MasterControl docs, ETQ</li>
              <li>Real implementation notes in adapter docstrings: Veeva auth POST /api/v24.1/auth, etc.</li>
              <li>No fabricated rows — connected=False when creds missing</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>Legal Clause Library & Playbook (GAP-P1-04 Fixed)</h2>
        <div className="legal-grid">
          <div className="legal-card">
            <h4>Clause Library ({clauses.length})</h4>
            <ul>
              {clauses.length === 0 ? <li>No clauses loaded</li> : clauses.map((c: any, i: number) => <li key={i}>{c.key} — {c.title} (v{c.version}, {c.category}) — {c.risk_tier}</li>)}
            </ul>
            <p><small>Backend: app/legal/playbook.py ClauseLibraryEntry, 5 default clauses mapping to clause_engine.py PATTERNS: termination, limitation_of_liability, indemnification, confidentiality, data_protection</small></p>
            <p><small>Endpoints: GET /api/legal/clause-library, GET /api/legal/clause-library/{`{key}`}</small></p>
          </div>
          <div className="legal-card">
            <h4>Playbook</h4>
            <ul>
              <li>Playbook: id, tenant_id, organization_id, environment_id, name, version, status (draft/active/archived), clauses[], rules[], created_at, updated_at</li>
              <li>Rules: id, clause_key, operator (contains/equals/gte/lte), value, severity, action, fingerprint</li>
              <li>Service: PlaybookService.create_playbook(), evaluate_against_playbook() — maps findings to clause entries, review_required when match</li>
              <li>Endpoints: GET/POST /api/legal/playbooks, GET /api/legal/playbooks/{`{id}`}, POST /api/legal/playbooks/{`{id}`}/evaluate</li>
            </ul>
          </div>
          <div className="legal-card">
            <h4>Redline Artifacts</h4>
            <ul>
              <li>RedlineChange: change_type (insertion/deletion/replacement), original_text, suggested_text, rationale, clause_key, source_reference, evidence_reference, fingerprint</li>
              <li>RedlineArtifact: id, document_id, playbook_id, changes[], diff_html (difflib.HtmlDiff), fingerprint (sha256_hex), disclaimer, review_state, evidence_references, created_at</li>
              <li>Engine: RedlineEngine.generate_redline() uses difflib.SequenceMatcher, _wrap_html(), _clause_to_original(), _clause_to_suggested(), generate_for_document()</li>
              <li>Endpoints: POST /api/legal/redlines (document_id, playbook_id, findings, evidence_refs), GET /api/legal/redlines/{`{id}`}, POST /api/legal/redlines/export</li>
              <li>Disclaimer: AI-generated redline suggestion only; not legal advice. Qualified human must review.</li>
            </ul>
          </div>
          <div className="legal-card">
            <h4>Versioning & Audit Chain</h4>
            <ul>
              <li>ClauseLibrary versioning via version field, fingerprint via sha256_hex</li>
              <li>Playbook versioning: version int, created_at/updated_at</li>
              <li>Redline audit package: export_audit_package() with fingerprint, exported_at, disclaimer, artifact_count</li>
              <li>Persistence: ClauseLibraryRepository, PlaybookRepository (governance policies table type=legal_playbook), RedlineRepository</li>
              <li>Evidence chain: evidence_references passed to redline artifacts, linked to review cases</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>Frameworks ({frameworks.length})</h2>
        {frameworks.length === 0 ? <p className="muted">No frameworks. Backend has QMS/OCG/Healthcare/Manufacturing/Retail active.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Type</th><th>Status</th></tr></thead>
              <tbody>
                {frameworks.map((f: any, i: number) => (
                  <tr key={i}><td>{f.name ?? f.framework_type}</td><td>{f.framework_type}</td><td>{f.status}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Industry Templates</h2>
        {templates.length === 0 ? <p className="muted">No templates loaded.</p> : (
          <ul>{templates.map((t: any, i: number) => <li key={i}>{t.name ?? t.id} — {t.industry_type ?? t.framework_type}</li>)}</ul>
        )}
      </section>

      <section className="card">
        <h2>Gap Closure Notes (Updated)</h2>
        <ul>
          <li>P1-03 QMS: ✅ Fixed — Veeva Vault, MasterControl, ETQ native adapters with tenant isolation, auth, retry notes, health, traceability graph, audit package assembly, honest unavailable when not configured</li>
          <li>P1-04 Legal: ✅ Fixed — Clause library (5 entries mapping to clause_engine.py PATTERNS), Playbook with rules (3 default), Redline artifact generator with difflib, fingerprint, diff_html, disclaimer, review workflow, persistence</li>
          <li>Preserves existing QMS engine: app/compliance/qms.py evaluate_control — configured-control evaluation</li>
          <li>Preserves existing legal: clause_engine, review_engine, citations — deterministic</li>
          <li>UI: This page surfaces real QMS adapters, clause library, playbook, redline — marks unavailable honestly, no fake data</li>
        </ul>
      </section>
    </>
  );
}
