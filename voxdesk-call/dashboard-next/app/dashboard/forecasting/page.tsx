"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { BacktestResult, ForecastProjection, ForecastResult } from "@/lib/api";

/**
 * Forecasting console.
 *
 * Every projection, interval and error metric on this page comes from the
 * backend: `POST /api/forecast` (app/api/forecast_routes.py ->
 * app/forecasting/service.py -> app/analytics/forecast.py) and
 * `POST /api/forecast/backtest`. Nothing is generated in the browser: an
 * earlier revision synthesised projections with pseudorandom noise and showed
 * them next to a real chart, which made a produced-by-nobody number look like a
 * backend result. That code is gone.
 *
 * The route requires `environment_id` and `model_version_id` (both UUIDs) and an
 * `idempotency_key` of at least 8 characters, so the form collects them
 * explicitly and the page renders a NOT_CONFIGURED state until they are given.
 */

interface UsagePoint {
  period: string;
  value: number;
}

interface Scenario {
  id: string;
  name: string;
  method: string;
  horizon: number;
  window: number;
  alpha: number;
  points: UsagePoint[];
  result: ForecastResult;
}

const METHODS: Array<{ value: string; label: string }> = [
  { value: "linear", label: "linear — trend with residual interval spread" },
  { value: "moving_average", label: "moving_average — trailing window" },
  { value: "exponential_smoothing", label: "exponential_smoothing — alpha" },
];

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isUuid(value: string): boolean {
  return UUID_RE.test(value.trim());
}

function nextPeriodLabel(last: string): string {
  const parsed = new Date(last);
  if (Number.isNaN(parsed.getTime())) return last;
  parsed.setUTCDate(parsed.getUTCDate() + 1);
  return parsed.toISOString().slice(0, 10);
}

function idempotencyKey(prefix: string): string {
  // `crypto.randomUUID` is an RFC 4122 identifier, not a data value: it only
  // keys the request so a retry cannot create a second execution record.
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}`;
  return `${prefix}-${suffix}`.slice(0, 200);
}

export default function ForecastingPage() {
  const [points, setPoints] = useState<UsagePoint[]>([
    { period: "2026-09-01", value: 100 },
    { period: "2026-09-02", value: 120 },
    { period: "2026-09-03", value: 115 },
    { period: "2026-09-04", value: 130 },
    { period: "2026-09-05", value: 125 },
    { period: "2026-09-06", value: 140 },
    { period: "2026-09-07", value: 135 },
  ]);
  const [method, setMethod] = useState("linear");
  const [horizon, setHorizon] = useState(7);
  const [windowSize, setWindowSize] = useState(3);
  const [alpha, setAlpha] = useState(0.5);
  const [environmentId, setEnvironmentId] = useState("");
  const [modelVersionId, setModelVersionId] = useState("");
  const [seriesReference, setSeriesReference] = useState("");

  const [result, setResult] = useState<ForecastResult | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [running, setRunning] = useState<null | "forecast" | "backtest">(null);
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [loading, setLoading] = useState(true);

  const configured = isUuid(environmentId) && isUuid(modelVersionId);
  const projections: ForecastProjection[] = result?.projections ?? [];
  const selectedScenario = scenarios.find((s) => s.id === selectedScenarioId) ?? null;

  // Probe the forecasting surface once so an unauthenticated / unprovisioned
  // workspace sees a clear state instead of an empty chart.
  useEffect(() => {
    let cancelled = false;
    api
      .specializedAgents()
      .then((agents) => {
        if (cancelled) return;
        const forecasting = agents.find((a) => a.type === "forecasting");
        setNotConfigured(!forecasting);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setError("Your session cannot read the specialized-agent registry. Sign in again or ask an administrator for analytics access.");
          return;
        }
        setError(err instanceof ApiError ? err.message : "Failed to load the forecasting agent registry");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const series = useMemo(
    () => points.filter((p) => p.period.trim() !== "" && Number.isFinite(p.value)),
    [points],
  );

  async function runForecast() {
    if (!configured) {
      setError("Enter the environment UUID and the model-version UUID first.");
      return;
    }
    if (series.length < 1) {
      setError("Add at least one observation before projecting.");
      return;
    }
    setError(null);
    setRunning("forecast");
    try {
      const response = await api.forecast({
        environment_id: environmentId.trim(),
        model_version_id: modelVersionId.trim(),
        idempotency_key: idempotencyKey("forecast"),
        series_reference: seriesReference.trim() || undefined,
        series,
        method,
        configuration: { window: windowSize, alpha },
        horizon,
      });
      setResult(response);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 403) setError("You do not have analytics permission for forecasting.");
        else if (err.code === "NOT_CONFIGURED") setNotConfigured(true);
        else setError(err.message);
      } else {
        setError("Forecast request failed.");
      }
    } finally {
      setRunning(null);
    }
  }

  async function runBacktest() {
    if (!configured) {
      setError("Enter the environment UUID and the model-version UUID first.");
      return;
    }
    if (series.length < 3) {
      setError("A backtest needs at least three observations so a test window remains after the train split.");
      return;
    }
    setError(null);
    setRunning("backtest");
    try {
      const response = await api.backtestForecast({
        environment_id: environmentId.trim(),
        model_version_id: modelVersionId.trim(),
        idempotency_key: idempotencyKey("backtest"),
        series_reference: seriesReference.trim() || undefined,
        series,
        method,
        configuration: { window: windowSize, alpha },
        test_size: Math.min(2, series.length - 1),
      });
      setBacktest(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Backtest request failed.");
    } finally {
      setRunning(null);
    }
  }

  function saveScenario() {
    if (!result) return;
    const id = result.execution_id;
    setScenarios((prev) => [
      ...prev.filter((s) => s.id !== id),
      { id, name: `${method} · h=${horizon}`, method, horizon, window: windowSize, alpha, points: [...series], result },
    ]);
    setSelectedScenarioId(id);
  }

  function addPoint() {
    const last = points[points.length - 1];
    // No generated value: the new row carries the last observation forward and
    // stays editable, so the operator types what actually happened.
    setPoints([
      ...points,
      { period: last ? nextPeriodLabel(last.period) : new Date().toISOString().slice(0, 10), value: last ? last.value : 0 },
    ]);
  }

  if (loading) return <div className="loading">Loading forecasting agent…</div>;

  return (
    <>
      <header className="page-head">
        <h1>Forecasting — projections, confidence intervals, backtests</h1>
        <p className="muted">
          Backend: <code>POST /api/forecast</code> and <code>POST /api/forecast/backtest</code> →
          app/forecasting/service.py → app/analytics/forecast.py (linear, moving_average,
          exponential_smoothing). Every number below is returned by that service.
        </p>
      </header>

      {notConfigured && (
        <div className="error-banner" role="status">
          NOT_CONFIGURED — the forecasting agent is not registered in
          app/specialized_agents/registry.py, so this surface cannot produce a projection.
          Nothing is substituted locally.
        </div>
      )}

      {error && (
        <div className="error-banner" role="alert">
          {error}{" "}
          <button onClick={() => setError(null)} style={{ marginLeft: 8 }}>
            ×
          </button>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr 320px", gap: 12 }}>
        <section className="card" style={{ padding: 12 }}>
          <h3>Scope</h3>
          <label style={{ fontSize: 11 }}>Environment ID (UUID)</label>
          <input
            type="text"
            data-testid="forecast-environment-id"
            value={environmentId}
            onChange={(e) => setEnvironmentId(e.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
            style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
          />
          <label style={{ fontSize: 11, display: "block", marginTop: 6 }}>Model version ID (UUID)</label>
          <input
            type="text"
            data-testid="forecast-model-version-id"
            value={modelVersionId}
            onChange={(e) => setModelVersionId(e.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
            style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
          />
          <label style={{ fontSize: 11, display: "block", marginTop: 6 }}>Series reference (optional)</label>
          <input
            type="text"
            value={seriesReference}
            onChange={(e) => setSeriesReference(e.target.value)}
            placeholder="usage:voice_minutes:2026-Q3"
            style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
          />

          <h3 style={{ marginTop: 16 }}>Series input — UsagePoint</h3>
          <p style={{ fontSize: 11, color: "#666" }}>
            Validated by the service: unique periods, strictly increasing dates, finite values.
          </p>
          <div style={{ maxHeight: 200, overflowY: "auto", border: "1px solid #e5e7eb", borderRadius: 6, padding: 6, marginTop: 8 }}>
            {points.map((p, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 60px 24px", gap: 4, marginBottom: 4 }}>
                <input
                  value={p.period}
                  onChange={(e) => {
                    const next = [...points];
                    next[i] = { ...next[i], period: e.target.value };
                    setPoints(next);
                  }}
                  style={{ padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
                />
                <input
                  type="number"
                  value={p.value}
                  onChange={(e) => {
                    const next = [...points];
                    next[i] = { ...next[i], value: e.target.value === "" ? Number.NaN : parseFloat(e.target.value) };
                    setPoints(next);
                  }}
                  style={{ padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
                />
                <button
                  onClick={() => setPoints(points.filter((_, idx) => idx !== i))}
                  aria-label={`Remove point ${p.period}`}
                  style={{ padding: 2, borderRadius: 4, border: "1px solid #ccc", background: "#fff", cursor: "pointer", fontSize: 10 }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <button onClick={addPoint} style={{ width: "100%", marginTop: 8, padding: 6, borderRadius: 6, border: "1px solid #ccc", background: "#fff", cursor: "pointer", fontSize: 12 }}>
            + Add point
          </button>

          <div style={{ marginTop: 16 }}>
            <h4>Method</h4>
            <select value={method} onChange={(e) => setMethod(e.target.value)} style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid #ccc" }}>
              {METHODS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginTop: 8 }}>
              <div>
                <label style={{ fontSize: 11 }}>Horizon</label>
                <input type="number" min={1} max={365} value={horizon} onChange={(e) => setHorizon(parseInt(e.target.value, 10) || 1)} style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }} />
              </div>
              <div>
                <label style={{ fontSize: 11 }}>Window</label>
                <input type="number" min={1} max={100} value={windowSize} onChange={(e) => setWindowSize(parseInt(e.target.value, 10) || 1)} style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }} />
              </div>
              <div>
                <label style={{ fontSize: 11 }}>Alpha</label>
                <input type="number" min={0} max={1} step={0.1} value={alpha} onChange={(e) => setAlpha(parseFloat(e.target.value) || 0)} style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }} />
              </div>
            </div>
            <button
              onClick={runForecast}
              disabled={running !== null || !configured}
              data-testid="run-forecast"
              style={{ width: "100%", marginTop: 12, padding: 8, background: configured ? "#10b981" : "#9ca3af", color: "#fff", border: "none", borderRadius: 6, cursor: configured ? "pointer" : "not-allowed" }}
            >
              {running === "forecast" ? "Projecting…" : "▶ Run projection"}
            </button>
            {!configured && (
              <p style={{ fontSize: 11, color: "#92400e" }}>
                NOT_CONFIGURED — both UUIDs are required by the route schema; the button stays disabled until they parse.
              </p>
            )}
          </div>
        </section>

        <section className="card" style={{ padding: 12 }}>
          <h3>Projections ({projections.length})</h3>
          {!result ? (
            <p style={{ fontSize: 12, color: "#888" }}>
              No projection yet. Run one: the response carries step, period, value, lower,
              upper and interval_status exactly as project_usage() computed them.
            </p>
          ) : (
            <>
              <div style={{ fontSize: 11, padding: 8, background: "#f9fafb", borderRadius: 4 }}>
                <div>Execution: {result.execution_id} · status {result.status}</div>
                <div>Method: {result.method} · horizon {result.horizon} · quality {result.data_quality}</div>
                <div>Interval: {result.interval_method ?? "not reported"} · review {result.review_state}</div>
                {result.disclaimer && <div style={{ marginTop: 4 }}>{result.disclaimer}</div>}
              </div>

              <div style={{ height: 220, position: "relative", border: "1px solid #e5e7eb", borderRadius: 6, padding: 10, background: "#fff", marginTop: 10, overflow: "hidden" }}>
                <svg width="100%" height="100%" viewBox="0 0 400 180" preserveAspectRatio="none" role="img" aria-label="Historical series and forecast projections">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <line key={i} x1={0} y1={i * 45} x2={400} y2={i * 45} stroke="#f3f4f6" strokeWidth={1} />
                  ))}
                  <polyline
                    points={series.map((p, i) => `${20 + (i / Math.max(1, series.length - 1)) * 150},${170 - p.value}`).join(" ")}
                    fill="none"
                    stroke="#6b7280"
                    strokeWidth={2}
                    strokeDasharray="4 2"
                  />
                  <polyline
                    points={projections.map((p, i) => `${190 + (i / Math.max(1, projections.length - 1)) * 190},${170 - p.value}`).join(" ")}
                    fill="none"
                    stroke="#3b82f6"
                    strokeWidth={2}
                  />
                  {projections
                    .filter((p) => p.lower !== null && p.upper !== null)
                    .map((p, i) => (
                      <line
                        key={`ci-${p.step}`}
                        x1={190 + (i / Math.max(1, projections.length - 1)) * 190}
                        x2={190 + (i / Math.max(1, projections.length - 1)) * 190}
                        y1={170 - (p.upper ?? 0)}
                        y2={170 - (p.lower ?? 0)}
                        stroke="#93c5fd"
                        strokeWidth={6}
                        strokeLinecap="round"
                        opacity={0.6}
                      />
                    ))}
                </svg>
                <div style={{ position: "absolute", bottom: 4, left: 8, fontSize: 10, color: "#666" }}>
                  Historical (dashed) → forecast (blue) · vertical bars = lower/upper when interval_status says available
                </div>
              </div>

              <div className="table-wrap" style={{ marginTop: 12, maxHeight: 260, overflowY: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Step</th>
                      <th>Period</th>
                      <th>Value</th>
                      <th>Lower</th>
                      <th>Upper</th>
                      <th>Interval</th>
                    </tr>
                  </thead>
                  <tbody>
                    {projections.map((p) => (
                      <tr key={p.step}>
                        <td>{p.step}</td>
                        <td>{p.period}</td>
                        <td>{p.value}</td>
                        <td>{p.lower ?? "—"}</td>
                        <td>{p.upper ?? "—"}</td>
                        <td>
                          <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: p.interval_status === "available" ? "#d1fae5" : "#fef3c7" }}>
                            {p.interval_status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
                <button onClick={saveScenario} style={{ padding: "6px 12px", background: "#8b5cf6", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 12 }}>
                  💾 Keep in comparison
                </button>
                <button onClick={runBacktest} disabled={running !== null} style={{ padding: "6px 12px", background: "#f59e0b", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 12 }}>
                  {running === "backtest" ? "Backtesting…" : "🧪 Run backtest"}
                </button>
              </div>
            </>
          )}
        </section>

        <section className="card" style={{ padding: 12 }}>
          <h3>Comparison ({scenarios.length})</h3>
          {scenarios.length === 0 ? (
            <p style={{ fontSize: 11, color: "#888" }}>
              Each saved entry is a real backend response kept in memory for side-by-side
              comparison. Nothing is stored server-side.
            </p>
          ) : (
            scenarios.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelectedScenarioId(s.id)}
                style={{ width: "100%", textAlign: "left", padding: 8, borderRadius: 6, border: selectedScenarioId === s.id ? "2px solid #000" : "1px solid #e5e7eb", background: "#fff", marginBottom: 6, cursor: "pointer" }}
              >
                <div style={{ fontSize: 12, fontWeight: 600 }}>{s.name}</div>
                <div style={{ fontSize: 10, color: "#666" }}>
                  {s.method} · h={s.horizon} · {s.result.projections.length} projections · quality {s.result.data_quality}
                </div>
              </button>
            ))
          )}

          {selectedScenario && (
            <div style={{ marginTop: 12, padding: 8, background: "#f5f3ff", borderRadius: 6, fontSize: 11 }}>
              <div style={{ fontWeight: 600 }}>{selectedScenario.name}</div>
              <div>Execution: {selectedScenario.result.execution_id}</div>
              <div>Values: {selectedScenario.result.projections.map((p) => p.value).join(", ")}</div>
            </div>
          )}

          <div style={{ marginTop: 16 }}>
            <h4>Backtest — honest evaluation</h4>
            {!backtest ? (
              <p style={{ fontSize: 11, color: "#666" }}>
                Splits the supplied series, forecasts the tail and reports the backend's own
                MAPE/RMSE with its mape_status.
              </p>
            ) : (
              <div style={{ marginTop: 8, padding: 8, background: "#fef3c7", borderRadius: 6, fontSize: 11 }}>
                <div style={{ fontWeight: 600 }}>Backtest — {backtest.method}</div>
                <div>
                  Train {backtest.train_points} · test {backtest.test_points}
                </div>
                <div>
                  MAPE {backtest.mape === null ? `— (${backtest.mape_status})` : `${backtest.mape}%`} · RMSE {backtest.rmse}
                </div>
                <div style={{ marginTop: 4 }}>
                  {backtest.comparisons.map((c) => `${c.period}: actual ${c.actual} / predicted ${c.predicted}`).join(" · ")}
                </div>
                {backtest.disclaimer && <div style={{ marginTop: 4 }}>{backtest.disclaimer}</div>}
              </div>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
