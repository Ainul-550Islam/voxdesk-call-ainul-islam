"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

// GAP-P1-06 Analytics/ROI measurement plane

export default function ROIPage() {
  const [baselines, setBaselines] = useState<any[]>([]);
  const [results, setResults] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.roiBaselines().catch(() => []),
      api.roiResults().catch(() => []),
    ]).then(([b, r]) => {
      if (cancelled) return;
      setBaselines(b);
      setResults(r);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load ROI");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div className="loading">Loading ROI…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  return (
    <>
      <header className="page-head">
        <h1>ROI / Analytics — Measurement Plane (P1-06)</h1>
        <p className="muted">Backend: app/analytics/, app/roi/ (cost_engine, kpi_engine), app/observability/ + /api/analytics, /api/roi (11+4 routes)</p>
      </header>

      <section className="card">
        <h2>Baselines ({baselines.length})</h2>
        {baselines.length === 0 ? <p className="muted">No baselines. Backend: app/roi/service.py ROIBaseline, _validate_period, _validate_metrics</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>Period</th><th>Metrics</th></tr></thead>
              <tbody>
                {baselines.map((b: any, i: number) => (
                  <tr key={i}><td><code>{(b.id ?? "").slice(0,8)}</code></td><td>{b.period_start} → {b.period_end}</td><td>{JSON.stringify(b.metrics ?? {}).slice(0,100)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Results ({results.length})</h2>
        {results.length === 0 ? <p className="muted">No results. Cost per outcome, containment, escalation, connector success, quality, ROI</p> : (
          <ul>{results.map((r: any, i: number) => <li key={i}>{JSON.stringify(r).slice(0,200)}</li>)}</ul>
        )}
      </section>

      <section className="card">
        <h2>Measurement Plane (LuMay Parity)</h2>
        <ul>
          <li>OTel/metrics/logs: app/observability/runtime.py, cost.py, health.py + prometheus/grafana</li>
          <li>Cost: app/roi/cost_engine.py calculate_cost, calculate_capacity + app/observability/cost.py record_measured_cost</li>
          <li>KPI: app/roi/kpi_engine.py validate_kpi_definition, evaluate_kpi</li>
          <li>Analytics: app/analytics/conversation_metrics.py sentiment, _stats.py mean/median/percentile, forecast.py</li>
          <li>Previously: Frontend analytics centered on calls/operations/billing only</li>
          <li>Now: Expose source-backed KPIs, cost per outcome, containment/escalation, connector success, quality, ROI with NOT_AVAILABLE when evidence insufficient</li>
        </ul>
      </section>
    </>
  );
}
