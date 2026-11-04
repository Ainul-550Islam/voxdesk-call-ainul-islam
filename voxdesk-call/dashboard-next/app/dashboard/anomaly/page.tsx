"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";

/**
 * Anomaly detection console.
 *
 * Every detection shown here is produced by `POST /api/anomaly/analyze`
 * (app/api/anomaly_routes.py → app/anomaly/service.py → detectors.py) and read
 * back through `GET /api/anomaly/{execution_id}`. An earlier revision invented
 * observations, confidences and explanations in the browser with pseudorandom
 * numbers while the copy around them pointed at the real detector — that
 * simulation is removed. The "Live" toggle now means "re-read the persisted
 * execution", not "make up a new reading".
 */

interface Observation {
  timestamp: string;
  value: number;
}

/** Shape of one entry in `results[]` of the analyze response (app/anomaly/schemas.py). */
interface DetectorResult {
  metric?: string;
  anomalous?: boolean;
  confidence?: number;
  severity?: string;
  observed_value?: number;
  expected_range?: [number, number] | number[];
  explanation?: string;
  quality_state?: string;
  review_required?: boolean;
  z_score?: number;
  threshold?: number;
}

interface Detection {
  id: string;
  metric: string;
  anomalous: boolean;
  confidence: number;
  severity: string;
  observedValue: number | null;
  expectedRange: [number, number] | null;
  explanation: string;
  qualityState: string;
  reviewRequired: boolean;
}

interface AnalysisResponse {
  execution_id?: string;
  metric?: string;
  detector?: string;
  quality_state?: string;
  review_required?: boolean;
  review_state?: string;
  methodology?: string;
  results?: DetectorResult[];
  alert_ids?: string[];
  review_case_id?: string | null;
}

const DETECTORS = ["z_score", "median_mad", "rolling_baseline", "sudden_change"] as const;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isUuid(value: string): boolean {
  return UUID_RE.test(value.trim());
}

function toDetection(executionId: string, index: number, raw: DetectorResult, fallbackMetric: string): Detection {
  const range = Array.isArray(raw.expected_range) && raw.expected_range.length >= 2
    ? ([Number(raw.expected_range[0]), Number(raw.expected_range[1])] as [number, number])
    : null;
  return {
    id: `${executionId}-${index}`,
    metric: String(raw.metric ?? fallbackMetric),
    anomalous: Boolean(raw.anomalous),
    confidence: Number(raw.confidence ?? 0),
    severity: String(raw.severity ?? "unknown"),
    observedValue: raw.observed_value === undefined ? null : Number(raw.observed_value),
    expectedRange: range,
    explanation: String(raw.explanation ?? ""),
    qualityState: String(raw.quality_state ?? "unknown"),
    reviewRequired: Boolean(raw.review_required),
  };
}

export default function AnomalyPage() {
  const [observations, setObservations] = useState<Observation[]>([
    { timestamp: "2026-09-29T00:00:00.000Z", value: 110 },
    { timestamp: "2026-09-29T01:00:00.000Z", value: 120 },
    { timestamp: "2026-09-29T02:00:00.000Z", value: 115 },
    { timestamp: "2026-09-29T03:00:00.000Z", value: 130 },
    { timestamp: "2026-09-29T04:00:00.000Z", value: 250 },
    { timestamp: "2026-09-29T05:00:00.000Z", value: 185 },
  ]);
  const [metric, setMetric] = useState("call_volume");
  const [detector, setDetector] = useState<string>("z_score");
  const [threshold, setThreshold] = useState(2.5);
  const [windowSize, setWindowSize] = useState(10);
  const [environmentId, setEnvironmentId] = useState("");
  const [modelVersionId, setModelVersionId] = useState("");

  const [executionId, setExecutionId] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [live, setLive] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const configured = isUuid(environmentId) && isUuid(modelVersionId);

  const applyResponse = useCallback((response: AnalysisResponse, fallbackMetric: string) => {
    const id = String(response.execution_id ?? "");
    setAnalysis(response);
    setExecutionId(id || null);
    setDetections(
      (Array.isArray(response.results) ? response.results : []).map((raw, index) =>
        toDetection(id || "run", index, raw, fallbackMetric),
      ),
    );
    setLastRefreshed(new Date().toISOString());
  }, []);

  const refreshExecution = useCallback(async () => {
    if (!executionId || !isUuid(environmentId)) return;
    try {
      const response = (await api.anomalyExecution(executionId, environmentId.trim())) as AnalysisResponse;
      applyResponse(response, metric);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not refresh the persisted analysis.");
      setLive(false);
    }
  }, [applyResponse, environmentId, executionId, metric]);

  useEffect(() => {
    if (!live || !executionId) {
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
      return;
    }
    timerRef.current = setInterval(() => {
      void refreshExecution();
    }, 5000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [live, executionId, refreshExecution]);

  // One probe so a workspace without the anomaly agent says so instead of
  // rendering an empty detector panel.
  useEffect(() => {
    let cancelled = false;
    api
      .specializedAgents()
      .then((agents) => {
        if (!cancelled) setNotConfigured(!agents.some((a) => a.type === "anomaly"));
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setError("Your session cannot read the specialized-agent registry. Sign in again or ask for analytics access.");
          return;
        }
        setError(err instanceof ApiError ? err.message : "Failed to load the anomaly agent registry");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function runDetection() {
    if (!configured) {
      setError("Enter the environment UUID and the model-version UUID first.");
      return;
    }
    const usable = observations.filter((o) => Number.isFinite(o.value) && o.timestamp.trim() !== "");
    if (usable.length === 0) {
      setError("Add at least one observation.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const response = (await api.anomalyAnalyze({
        environment_id: environmentId.trim(),
        model_version_id: modelVersionId.trim(),
        // Request key only; it never becomes a data value.
        idempotency_key:
          typeof crypto !== "undefined" && "randomUUID" in crypto
            ? `anomaly-${crypto.randomUUID()}`
            : `anomaly-${Date.now()}`,
        metric,
        observations: usable.map((o) => ({ metric, observed_at: o.timestamp, value: o.value })),
        configuration: {
          detector: detector as (typeof DETECTORS)[number],
          threshold,
          minimum_history: Math.max(2, Math.min(windowSize, usable.length)),
          rolling_window: Math.max(2, Math.min(windowSize, Math.max(2, usable.length - 1))),
        },
      })) as AnalysisResponse;
      applyResponse(response, metric);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 403) setError("You do not have analytics permission for anomaly analysis.");
        else if (err.code === "NOT_CONFIGURED") setNotConfigured(true);
        else setError(err.message);
      } else {
        setError("Anomaly analysis failed.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  function updateObservation(index: number, patch: Partial<Observation>) {
    setObservations((prev) => prev.map((o, i) => (i === index ? { ...o, ...patch } : o)));
  }

  return (
    <>
      <header className="page-head">
        <h1>Anomaly detection — detectors, sensitivity, explained routing</h1>
        <p className="muted">
          Backend: <code>POST /api/anomaly/analyze</code> → app/anomaly/service.py → detectors.py
          (z_score, median_mad, rolling_baseline, sudden_change), persisted by persistence.py and
          alerted by alerting.py. <code>GET /api/anomaly/&#123;execution_id&#125;</code> re-reads a run.
        </p>
      </header>

      {notConfigured && (
        <div className="error-banner" role="status">
          NOT_CONFIGURED — no anomaly agent is registered in app/specialized_agents/registry.py.
          No detector output is fabricated in its place.
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

      <div style={{ display: "grid", gridTemplateColumns: "340px 1fr 340px", gap: 12 }}>
        <section className="card" style={{ padding: 12 }}>
          <h3>Scope &amp; observations</h3>
          <label style={{ fontSize: 11 }}>Environment ID (UUID)</label>
          <input
            type="text"
            placeholder="uuid"
            data-testid="anomaly-environment-id"
            value={environmentId}
            onChange={(e) => setEnvironmentId(e.target.value)}
            style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
          />
          <label style={{ fontSize: 11, display: "block", marginTop: 6 }}>Model version ID (UUID)</label>
          <input
            type="text"
            placeholder="uuid"
            data-testid="anomaly-model-version-id"
            value={modelVersionId}
            onChange={(e) => setModelVersionId(e.target.value)}
            style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
          />

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <select value={metric} onChange={(e) => setMetric(e.target.value)} style={{ flex: 1, padding: 6, borderRadius: 4, border: "1px solid #ccc", fontSize: 12 }}>
              <option value="call_volume">call_volume</option>
              <option value="avg_handle_time">avg_handle_time</option>
              <option value="conversion_rate">conversion_rate</option>
              <option value="error_rate">error_rate</option>
            </select>
          </div>

          <div style={{ maxHeight: 210, overflowY: "auto", border: "1px solid #e5e7eb", borderRadius: 6, padding: 6, marginTop: 8 }}>
            {observations.map((o, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 62px 24px", gap: 4, marginBottom: 4 }}>
                <input
                  value={o.timestamp}
                  aria-label={`Observation ${i + 1} timestamp`}
                  onChange={(e) => updateObservation(i, { timestamp: e.target.value })}
                  style={{ padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 10 }}
                />
                <input
                  type="number"
                  aria-label={`Observation ${i + 1} value`}
                  value={Number.isFinite(o.value) ? o.value : ""}
                  onChange={(e) => updateObservation(i, { value: e.target.value === "" ? Number.NaN : parseFloat(e.target.value) })}
                  style={{ padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}
                />
                <button
                  onClick={() => setObservations((prev) => prev.filter((_, idx) => idx !== i))}
                  aria-label={`Remove observation ${i + 1}`}
                  style={{ padding: 2, borderRadius: 4, border: "1px solid #ccc", background: "#fff", cursor: "pointer", fontSize: 10 }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <button
            onClick={() =>
              setObservations((prev) => {
                const last = prev[prev.length - 1];
                const nextTime = last ? new Date(new Date(last.timestamp).getTime() + 60 * 60 * 1000) : new Date();
                // Carries the previous value forward as an editable row: a new
                // observation is something an operator measures, not something
                // this page invents.
                return [...prev, { timestamp: nextTime.toISOString(), value: last ? last.value : 0 }];
              })
            }
            style={{ width: "100%", marginTop: 8, padding: 6, borderRadius: 6, border: "1px solid #ccc", background: "#fff", cursor: "pointer", fontSize: 12 }}
          >
            + Add observation
          </button>

          <div style={{ marginTop: 16 }}>
            <h4>Detector configuration</h4>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <label style={{ fontSize: 11 }}>Detector</label>
                <select value={detector} onChange={(e) => setDetector(e.target.value)} style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }}>
                  {DETECTORS.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ fontSize: 11 }}>Window</label>
                <input type="number" min={2} max={100} value={windowSize} onChange={(e) => setWindowSize(parseInt(e.target.value, 10) || 2)} style={{ width: "100%", padding: 4, borderRadius: 4, border: "1px solid #ccc", fontSize: 11 }} />
              </div>
            </div>
            <label style={{ fontSize: 11, display: "block", marginTop: 8 }}>Threshold: {threshold.toFixed(2)}</label>
            <input type="range" min={0.5} max={6} step={0.1} value={threshold} onChange={(e) => setThreshold(parseFloat(e.target.value))} style={{ width: "100%" }} />
            <button
              onClick={runDetection}
              disabled={submitting || !configured}
              data-testid="run-anomaly"
              style={{ width: "100%", marginTop: 12, padding: 8, background: configured ? "#8b5cf6" : "#9ca3af", color: "#fff", border: "none", borderRadius: 6, cursor: configured ? "pointer" : "not-allowed" }}
            >
              {submitting ? "Analyzing…" : "▶ Run detection"}
            </button>
            {!configured && (
              <p style={{ fontSize: 11, color: "#92400e" }}>
                NOT_CONFIGURED — the route requires environment and model-version UUIDs.
              </p>
            )}
          </div>
        </section>

        <section className="card" style={{ padding: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3>Detections ({detections.length})</h3>
            <button
              onClick={() => setLive((v) => !v)}
              disabled={!executionId}
              style={{ padding: "4px 10px", borderRadius: 6, border: "none", background: live ? "#ef4444" : "#10b981", color: "#fff", cursor: executionId ? "pointer" : "not-allowed", fontSize: 11 }}
            >
              {live ? "⏸ Stop re-reading" : "▶ Re-read every 5s"}
            </button>
          </div>
          <p style={{ fontSize: 11, color: "#666" }}>
            “Re-read” polls <code>GET /api/anomaly/&#123;execution_id&#125;</code> for this run. It does not
            invent new observations{lastRefreshed ? ` · last read ${new Date(lastRefreshed).toLocaleTimeString()}` : ""}.
          </p>

          {detections.length === 0 ? (
            <p style={{ fontSize: 12, color: "#888" }}>
              Nothing analyzed yet. Run a detection: the results below come from the persisted
              execution, never from local arithmetic.
            </p>
          ) : (
            <div style={{ maxHeight: 560, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
              {detections.map((det) => (
                <div
                  key={det.id}
                  style={{
                    padding: 12,
                    borderRadius: 8,
                    border: `2px solid ${det.anomalous ? (det.severity === "critical" ? "#ef4444" : "#f59e0b") : "#10b981"}`,
                    background: det.anomalous ? "#fef3c7" : "#ecfdf5",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 12, fontWeight: 700 }}>
                      {det.metric} — {det.anomalous ? "ANOMALY" : "NORMAL"}
                    </span>
                    <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "#111", color: "#fff" }}>
                      {det.severity} · confidence {det.confidence.toFixed(2)}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, marginTop: 6 }}>
                    Observed {det.observedValue ?? "—"}
                    {det.expectedRange ? ` vs expected ${det.expectedRange[0]}–${det.expectedRange[1]}` : ""}
                    {det.explanation ? ` — ${det.explanation}` : ""}
                  </div>
                  <div style={{ fontSize: 10, color: "#666", marginTop: 6 }}>
                    quality {det.qualityState} · review {det.reviewRequired ? "REQUIRED" : "NOT_REQUIRED"} · id {det.id}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card" style={{ padding: 12 }}>
          <h3>Run metadata</h3>
          {!analysis ? (
            <p style={{ fontSize: 11, color: "#888" }}>No execution yet.</p>
          ) : (
            <div style={{ fontSize: 11, background: "#f9fafb", padding: 8, borderRadius: 4, fontFamily: "monospace", wordBreak: "break-all" }}>
              <div>execution_id: {analysis.execution_id ?? "—"}</div>
              <div>detector: {analysis.detector ?? detector}</div>
              <div>metric: {analysis.metric ?? metric}</div>
              <div>quality_state: {analysis.quality_state ?? "—"}</div>
              <div>review_state: {analysis.review_state ?? "—"}</div>
              <div>alerts: {(analysis.alert_ids ?? []).join(", ") || "none"}</div>
              <div>review_case_id: {analysis.review_case_id ?? "none"}</div>
            </div>
          )}

          {analysis?.methodology && (
            <div style={{ marginTop: 12, fontSize: 11 }}>
              <h4>Methodology</h4>
              <p>{analysis.methodology}</p>
            </div>
          )}

          <div style={{ marginTop: 16, fontSize: 11, color: "#666" }}>
            <h4 style={{ color: "#111" }}>Persistence</h4>
            <div>AnomalyRunRecord / AnomalyResultRecord / AnomalyAlertRecord — persistence.py</div>
            <div>publish_alerts + reconcile_alert_delivery — alerting.py</div>
            <div>Order validation: _validate_order rejects out-of-order timestamps.</div>
          </div>
        </section>
      </div>
    </>
  );
}
