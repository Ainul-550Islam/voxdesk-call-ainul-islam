"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface ExperimentVariant {
  id: string;
  name: string;
  version: number;
  weight: number;
  call_count: number;
  success_count: number;
  avg_duration_seconds: number;
  avg_latency_ms: number;
  avg_sentiment_score: number;
}

interface ExperimentRecord {
  id: string;
  name: string;
  description: string;
  agent_id: string;
  status: string;
  promoted_variant_id: string | null;
  variants: ExperimentVariant[];
  created_at: string | null;
}

interface VariantMetricItem {
  variant_id: string;
  name: string;
  version: number;
  weight: number;
  call_count: number;
  success_count: number;
  success_rate: number;
  avg_duration: number;
  avg_latency_ms: number;
  avg_sentiment_score: number;
}

interface ExperimentMetricsResponse {
  experiment_id: string;
  status: string;
  total_calls: number;
  confidence_score: number;
  sample_sufficiency: boolean;
  winning_variant_id: string | null;
  variants: VariantMetricItem[];
}

export default function ExperimentsPage() {
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);
  const [selectedExp, setSelectedExp] = useState<ExperimentRecord | null>(null);
  const [metrics, setMetrics] = useState<ExperimentMetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [expName, setExpName] = useState("");
  const [expDesc, setExpDesc] = useState("");
  const [agentId, setAgentId] = useState("agent-default");
  const [variantAVersion, setVariantAVersion] = useState(1);
  const [variantAWeight, setVariantAWeight] = useState(50);
  const [variantBVersion, setVariantBVersion] = useState(2);
  const [variantBWeight, setVariantBWeight] = useState(50);

  const loadExperiments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await request<{ experiments: ExperimentRecord[] }>(
        "/api/experiments",
      );
      setExperiments(res.experiments);
      if (res.experiments.length > 0 && !selectedExp) {
        setSelectedExp(res.experiments[0]);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load experiments",
      );
    } finally {
      setLoading(false);
    }
  }, [selectedExp]);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    void loadExperiments();
  }, [loadExperiments]);

  useEffect(() => {
    if (!selectedExp || !getToken()) return;
    let cancelled = false;
    request<ExperimentMetricsResponse>(
      `/api/experiments/${encodeURIComponent(selectedExp.id)}/metrics`,
    )
      .then((res) => {
        if (!cancelled) setMetrics(res);
      })
      .catch(() => {
        if (!cancelled) setMetrics(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedExp]);

  async function handleCreateExperiment(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!expName.trim()) {
      setError("Experiment name is required.");
      return;
    }
    if (variantAWeight + variantBWeight !== 100) {
      setError("Variant traffic weights must sum to 100%.");
      return;
    }
    try {
      const created = await request<ExperimentRecord>("/api/experiments", {
        method: "POST",
        body: JSON.stringify({
          name: expName.trim(),
          description: expDesc.trim(),
          agent_id: agentId.trim() || "agent-default",
          variants: [
            {
              name: "Control (A)",
              weight: variantAWeight,
              is_control: true,
              config: { version: variantAVersion },
            },
            {
              name: "Challenger (B)",
              weight: variantBWeight,
              is_control: false,
              config: { version: variantBVersion },
            },
          ],
        }),
      });
      setExpName("");
      setExpDesc("");
      setExperiments((prev) => [created, ...prev]);
      setSelectedExp(created);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to create experiment",
      );
    }
  }

  async function handlePromoteVariant(experimentId: string, variantId: string) {
    setError(null);
    try {
      await request(
        `/api/experiments/${encodeURIComponent(experimentId)}/promote`,
        {
          method: "POST",
          body: JSON.stringify({ variant_id: variantId }),
        },
      );
      await loadExperiments();
      const updatedMetrics = await request<ExperimentMetricsResponse>(
        `/api/experiments/${encodeURIComponent(experimentId)}/metrics`,
      );
      setMetrics(updatedMetrics);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to promote variant",
      );
    }
  }

  return (
    <div data-testid="experiments-page">
      <header className="page-head">
        <div>
          <h1>A/B Traffic Split Experiments</h1>
          <p className="muted">
            Split live call traffic across pinned AgentVersions, compare conversion and latency with statistical confidence, and promote winners.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      <section className="card" data-testid="create-experiment-card">
        <h2>Create A/B Experiment</h2>
        <form
          onSubmit={handleCreateExperiment}
          style={{ display: "grid", gap: "0.75rem" }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <label>
              Experiment Name
              <input
                type="text"
                data-testid="experiment-name-input"
                placeholder="Prompt Conciseness v1 vs v2"
                value={expName}
                onChange={(e) => setExpName(e.target.value)}
              />
            </label>
            <label>
              Agent ID
              <input
                type="text"
                data-testid="experiment-agent-input"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
              />
            </label>
            <label>
              Control Version (A)
              <input
                type="number"
                min={1}
                value={variantAVersion}
                onChange={(e) => setVariantAVersion(Number(e.target.value))}
              />
            </label>
            <label>
              Control Weight (%)
              <input
                type="number"
                min={0}
                max={100}
                data-testid="variant-a-weight-input"
                value={variantAWeight}
                onChange={(e) => {
                  const w = Number(e.target.value);
                  setVariantAWeight(w);
                  setVariantBWeight(Math.max(0, 100 - w));
                }}
              />
            </label>
            <label>
              Challenger Version (B)
              <input
                type="number"
                min={1}
                value={variantBVersion}
                onChange={(e) => setVariantBVersion(Number(e.target.value))}
              />
            </label>
            <label>
              Challenger Weight (%)
              <input
                type="number"
                min={0}
                max={100}
                data-testid="variant-b-weight-input"
                value={variantBWeight}
                onChange={(e) => setVariantBWeight(Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            Hypothesis / Description
            <input
              type="text"
              placeholder="Shorter greeting reduces caller drop-off"
              value={expDesc}
              onChange={(e) => setExpDesc(e.target.value)}
            />
          </label>
          <div>
            <button
              type="submit"
              className="btn primary"
              data-testid="create-experiment-submit-btn"
            >
              Start A/B Experiment
            </button>
          </div>
        </form>
      </section>

      <section className="card" data-testid="experiments-list-card">
        <h2>Active &amp; Completed Experiments ({experiments.length})</h2>
        {loading ? (
          <div className="loading">Loading experiments…</div>
        ) : experiments.length === 0 ? (
          <div className="empty">No A/B experiments created yet.</div>
        ) : (
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {experiments.map((exp) => (
              <button
                key={exp.id}
                type="button"
                className={selectedExp?.id === exp.id ? "btn primary" : "btn"}
                onClick={() => setSelectedExp(exp)}
              >
                {exp.name} ({exp.status})
              </button>
            ))}
          </div>
        )}
      </section>

      {selectedExp && metrics ? (
        <section className="card" data-testid="experiment-metrics-card">
          <h2>
            Metrics &amp; Statistical Confidence — {selectedExp.name}
          </h2>
          <dl className="kv">
            <div>
              <dt>Status</dt>
              <dd>{metrics.status}</dd>
            </div>
            <div>
              <dt>Total Calls Sampled</dt>
              <dd>{metrics.total_calls}</dd>
            </div>
            <div>
              <dt>Statistical Confidence</dt>
              <dd data-testid="experiment-confidence-value">
                {Math.round(metrics.confidence_score * 100)}%
              </dd>
            </div>
            <div>
              <dt>Sample Sufficiency</dt>
              <dd>{metrics.sample_sufficiency ? "Sufficient (≥20)" : "Collecting samples"}</dd>
            </div>
          </dl>

          <table className="table" style={{ marginTop: "1rem" }}>
            <thead>
              <tr>
                <th>Variant</th>
                <th>Version</th>
                <th>Traffic Split</th>
                <th>Calls</th>
                <th>Success Rate</th>
                <th>Avg Duration</th>
                <th>Avg Latency</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {metrics.variants.map((v) => (
                <tr key={v.variant_id}>
                  <td>
                    <strong>{v.name}</strong>
                    {metrics.winning_variant_id === v.variant_id ? " ★ Winner" : ""}
                  </td>
                  <td>v{v.version}</td>
                  <td>{v.weight}%</td>
                  <td>{v.call_count}</td>
                  <td>{Math.round(v.success_rate * 100)}%</td>
                  <td>{v.avg_duration}s</td>
                  <td>{v.avg_latency_ms}ms</td>
                  <td>
                    <button
                      type="button"
                      className="btn primary"
                      data-testid={`promote-variant-${v.variant_id}`}
                      onClick={() =>
                        handlePromoteVariant(selectedExp.id, v.variant_id)
                      }
                    >
                      Promote to 100%
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </div>
  );
}
