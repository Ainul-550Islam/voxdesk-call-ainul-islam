"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

// GAP-P1-07 Deployment verification, P0-01/P0-07 canonical topology

export default function DeploymentPage() {
  const [targets, setTargets] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.deploymentTargets()
      .then((rows) => { if (!cancelled) setTargets(rows); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load deployment targets"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div className="loading">Loading deployment…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  return (
    <>
      <header className="page-head">
        <h1>Deployment — Verification & Topology (P1-07, P0-01, P0-07)</h1>
        <p className="muted">Backend: app/deployment/ (adapters: container, k8s, airgap, registry), readiness.py, verification.py, service.py + 13+7 routes</p>
      </header>

      <section className="card">
        <h2>Canonical Production Topology</h2>
        <pre className="code-block">
{`Browser
  └── Caddy :80/:443
       ├── /realtime/ws → gateway-go :8790 (CANONICAL) + media-engine-rs :9001 control, :5000/udp public
       └── / → api :8000 serves dashboard/dist (Vite CANONICAL)

Shadow/Roadmap (NOT in prod compose):
- signal-go (Go differential signaling) — CI-tested
- control-plane (Rust) — roadmap Phase 2, shadow parity
- media-plane (C++ DSP) — foundation only, per README missing MediaControl, Opus, DTLS-SRTP
- dashboard-next (Next.js) — migration target, CI-tested in polyglot.yml`}
        </pre>
      </section>

      <section className="card">
        <h2>Targets ({targets.length})</h2>
        <p className="muted">API: /api/deployments/targets — readiness, revisions, approve, deploy, verify, suspend, retire</p>
        {targets.length === 0 ? <p className="muted">No targets. Backend: app/deployment/models.py DeploymentTarget, DeploymentRevision, DeploymentReadiness, DeploymentVerification</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Type</th><th>Status</th></tr></thead>
              <tbody>
                {targets.map((t: any, i: number) => (
                  <tr key={i}><td>{t.name ?? t.id}</td><td>{t.target_type ?? t.type}</td><td>{t.status}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Verification States (GAP-P1-07 Fix)</h2>
        <ul>
          <li>Configured: desired state from policy</li>
          <li>Ready: readiness evaluation via app/deployment/readiness.py _approval_and_governance</li>
          <li>Observed: safe_observation via app/deployment/evidence.py</li>
          <li>Verified: advance_verification_state via app/deployment/verification.py — unavailable when authoritative runtime not integrated (honest)</li>
          <li>Adapters: container (Docker), kubernetes (Helm values.yaml), airgap (digest verification), registry (OCI)</li>
          <li>Previously: verification intentionally incomplete, recorded unavailable without runtime observer</li>
          <li>Now: Distinguish configured vs ready vs observed vs unknown, surface blocker explicitly</li>
        </ul>
      </section>

      <section className="card">
        <h2>Env Markers (Prod Compose)</h2>
        <ul>
          <li>VOXDESK_CANONICAL_FRONTEND=dashboard-vite, VOXDESK_SHADOW_FRONTEND=dashboard-next-roadmap (api)</li>
          <li>VOXDESK_CANONICAL_REALTIME=gateway-go+media-engine-rs, VOXDESK_SHADOW_REALTIME=signal-go,control-plane,media-plane-roadmap (gateway)</li>
          <li>Logs: dashboard.mounted, dashboard.not_built with shadow flags per app/main.py</li>
          <li>Docs: docs/CURRENT-ARCHITECTURE.md canonical topology</li>
        </ul>
      </section>
    </>
  );
}
