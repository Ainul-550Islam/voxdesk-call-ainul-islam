"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, BASE_URL, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface WidgetSpec {
  id?: string;
  title: string;
  metric: string;
  dimension: string;
  chart_type: string;
  filters: Record<string, string>;
}

interface CustomDashboard {
  id: string;
  name: string;
  description: string;
  widgets: WidgetSpec[];
  global_filters: Record<string, string>;
  share_enabled: boolean;
  share_token: string | null;
  share_url: string | null;
  created_at: string | null;
}

interface EvaluatedWidget extends WidgetSpec {
  value: unknown;
  series: Array<{
    key: string;
    label: string;
    value: unknown;
    total_calls: number;
    success_rate: number;
    avg_duration_seconds: number;
    p95_duration_seconds: number;
    avg_cost_per_call_usd: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    transfer_rate: number;
    voicemail_rate: number;
  }>;
}

interface EvaluateResponse {
  dashboard: CustomDashboard;
  summary: {
    total_calls: number;
    success_rate: number;
    avg_duration_seconds: number;
    p95_duration_seconds: number;
    avg_cost_per_call_usd: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    transfer_rate: number;
    voicemail_rate: number;
  };
  widgets: EvaluatedWidget[];
}

const METRICS = [
  { value: "success_rate", label: "Success Rate (%)" },
  { value: "total_calls", label: "Total Calls" },
  { value: "avg_duration_seconds", label: "Avg Duration (s)" },
  { value: "p95_duration_seconds", label: "P95 Duration (s)" },
  { value: "avg_cost_per_call_usd", label: "Cost per Call (USD)" },
  { value: "total_cost_usd", label: "Total Cost (USD)" },
  { value: "latency_p50_ms", label: "Latency P50 (ms)" },
  { value: "latency_p95_ms", label: "Latency P95 (ms)" },
  { value: "transfer_rate", label: "Transfer Rate (%)" },
  { value: "voicemail_rate", label: "Voicemail Rate (%)" },
  { value: "disconnect_reasons", label: "Disconnect Reasons" },
  { value: "sentiment", label: "Caller Sentiment" },
];

const DIMENSIONS = [
  { value: "day", label: "Day" },
  { value: "agent", label: "Agent" },
  { value: "version", label: "Agent Version" },
  { value: "number", label: "Phone Number" },
  { value: "disconnect_reason", label: "Disconnect Reason" },
  { value: "sentiment", label: "Sentiment" },
  { value: "status", label: "Call Status" },
];

const CHART_TYPES = ["bar", "line", "kpi", "table", "pie", "area"];

export default function CustomDashboardsPage() {
  const [dashboards, setDashboards] = useState<CustomDashboard[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evaluated, setEvaluated] = useState<EvaluateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Builder state
  const [dashName, setDashName] = useState("");
  const [dashDesc, setDashDesc] = useState("");
  const [draftWidgets, setDraftWidgets] = useState<WidgetSpec[]>([
    {
      title: "Success Rate by Agent",
      metric: "success_rate",
      dimension: "agent",
      chart_type: "bar",
      filters: {},
    },
    {
      title: "P95 Latency by Day",
      metric: "latency_p95_ms",
      dimension: "day",
      chart_type: "line",
      filters: {},
    },
  ]);
  const [newWidgetTitle, setNewWidgetTitle] = useState("");
  const [newWidgetMetric, setNewWidgetMetric] = useState("success_rate");
  const [newWidgetDimension, setNewWidgetDimension] = useState("day");
  const [newWidgetChart, setNewWidgetChart] = useState("bar");
  const [newWidgetFilterAgent, setNewWidgetFilterAgent] = useState("");

  const loadDashboards = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await request<{ dashboards: CustomDashboard[] }>(
        "/api/v1/analytics/dashboards",
      );
      setDashboards(res.dashboards);
      if (res.dashboards.length > 0 && !selectedId) {
        setSelectedId(res.dashboards[0].id);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load custom dashboards",
      );
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    void loadDashboards();
  }, [loadDashboards]);

  useEffect(() => {
    if (!selectedId || !getToken()) return;
    let cancelled = false;
    request<EvaluateResponse>(
      `/api/v1/analytics/dashboards/${encodeURIComponent(selectedId)}/evaluate`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    )
      .then((res) => {
        if (!cancelled) setEvaluated(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Failed to evaluate dashboard",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  function handleAddWidget() {
    const title = newWidgetTitle.trim() || `${newWidgetMetric} by ${newWidgetDimension}`;
    const filters: Record<string, string> = {};
    if (newWidgetFilterAgent.trim()) {
      filters.agent_id = newWidgetFilterAgent.trim();
    }
    setDraftWidgets((prev) => [
      ...prev,
      {
        title,
        metric: newWidgetMetric,
        dimension: newWidgetDimension,
        chart_type: newWidgetChart,
        filters,
      },
    ]);
    setNewWidgetTitle("");
    setNewWidgetFilterAgent("");
  }

  function handleRemoveDraftWidget(idx: number) {
    setDraftWidgets((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleSaveDashboard() {
    if (!dashName.trim()) {
      setError("Please enter a dashboard name.");
      return;
    }
    setError(null);
    try {
      const created = await request<CustomDashboard>(
        "/api/v1/analytics/dashboards",
        {
          method: "POST",
          body: JSON.stringify({
            name: dashName.trim(),
            description: dashDesc.trim(),
            widgets: draftWidgets,
            global_filters: {},
            share_enabled: false,
          }),
        },
      );
      setDashName("");
      setDashDesc("");
      setDashboards((prev) => [created, ...prev]);
      setSelectedId(created.id);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to save dashboard",
      );
    }
  }

  async function handleToggleShare(dashboardId: string, enable: boolean) {
    setError(null);
    try {
      const shareRes = await request<{
        dashboard_id: string;
        share_enabled: boolean;
        share_token: string | null;
        share_url: string | null;
      }>(`/api/v1/analytics/dashboards/${encodeURIComponent(dashboardId)}/share`, {
        method: "POST",
        body: JSON.stringify({ enabled: enable, rotate: false }),
      });
      setDashboards((prev) =>
        prev.map((d) =>
          d.id === dashboardId
            ? {
                ...d,
                share_enabled: shareRes.share_enabled,
                share_token: shareRes.share_token,
                share_url: shareRes.share_url,
              }
            : d,
        ),
      );
      if (evaluated && evaluated.dashboard.id === dashboardId) {
        setEvaluated({
          ...evaluated,
          dashboard: {
            ...evaluated.dashboard,
            share_enabled: shareRes.share_enabled,
            share_token: shareRes.share_token,
            share_url: shareRes.share_url,
          },
        });
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to update share link",
      );
    }
  }

  return (
    <div data-testid="custom-dashboards-page">
      <header className="page-head">
        <div>
          <h1>Custom Analytics Dashboards</h1>
          <p className="muted">
            Build tenant-scoped widgets across latency, cost, sentiment, transfer rate, and disconnect reasons.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      <section className="card" data-testid="widget-builder-card">
        <h2>Create Custom Dashboard &amp; Widget Builder</h2>
        <div style={{ display: "grid", gap: "0.75rem", marginBottom: "1rem" }}>
          <label>
            Dashboard Name
            <input
              type="text"
              data-testid="dashboard-name-input"
              placeholder="e.g. Q4 Voice Quality & Cost"
              value={dashName}
              onChange={(e) => setDashName(e.target.value)}
            />
          </label>
          <label>
            Description
            <input
              type="text"
              placeholder="Optional description"
              value={dashDesc}
              onChange={(e) => setDashDesc(e.target.value)}
            />
          </label>
        </div>

        <h3>Add Widget</h3>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
            gap: "0.75rem",
            alignItems: "end",
          }}
        >
          <label>
            Widget Title
            <input
              type="text"
              data-testid="widget-title-input"
              placeholder="Latency by Version"
              value={newWidgetTitle}
              onChange={(e) => setNewWidgetTitle(e.target.value)}
            />
          </label>
          <label>
            Metric
            <select
              data-testid="widget-metric-select"
              value={newWidgetMetric}
              onChange={(e) => setNewWidgetMetric(e.target.value)}
            >
              {METRICS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Group By Dimension
            <select
              data-testid="widget-dimension-select"
              value={newWidgetDimension}
              onChange={(e) => setNewWidgetDimension(e.target.value)}
            >
              {DIMENSIONS.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Chart Type
            <select
              data-testid="widget-chart-select"
              value={newWidgetChart}
              onChange={(e) => setNewWidgetChart(e.target.value)}
            >
              {CHART_TYPES.map((ct) => (
                <option key={ct} value={ct}>
                  {ct.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label>
            Filter Agent ID (optional)
            <input
              type="text"
              placeholder="UUID"
              value={newWidgetFilterAgent}
              onChange={(e) => setNewWidgetFilterAgent(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn"
            data-testid="add-widget-btn"
            onClick={handleAddWidget}
          >
            + Add Widget
          </button>
        </div>

        <div style={{ marginTop: "1rem" }}>
          <h4>Configured Widgets ({draftWidgets.length})</h4>
          <ul>
            {draftWidgets.map((w, idx) => (
              <li key={idx} style={{ marginBottom: "0.35rem" }}>
                <strong>{w.title}</strong> — <code>{w.metric}</code> grouped by{" "}
                <code>{w.dimension}</code> ({w.chart_type}){" "}
                <button
                  type="button"
                  className="btn"
                  onClick={() => handleRemoveDraftWidget(idx)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="btn primary"
            data-testid="save-dashboard-btn"
            onClick={handleSaveDashboard}
          >
            Save Custom Dashboard
          </button>
        </div>
      </section>

      <section className="card" data-testid="saved-dashboards-card">
        <h2>Saved Dashboards ({dashboards.length})</h2>
        {loading ? (
          <div className="loading">Loading dashboards…</div>
        ) : dashboards.length === 0 ? (
          <div className="empty">No custom dashboards saved yet.</div>
        ) : (
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {dashboards.map((d) => (
              <button
                key={d.id}
                type="button"
                className={selectedId === d.id ? "btn primary" : "btn"}
                onClick={() => setSelectedId(d.id)}
              >
                {d.name} ({d.widgets.length})
              </button>
            ))}
          </div>
        )}
      </section>

      {evaluated ? (
        <section className="card" data-testid="evaluated-dashboard-section">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "0.75rem",
            }}
          >
            <div>
              <h2>{evaluated.dashboard.name}</h2>
              <p className="muted">{evaluated.dashboard.description}</p>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button
                type="button"
                className="btn"
                data-testid="toggle-share-btn"
                onClick={() =>
                  handleToggleShare(
                    evaluated.dashboard.id,
                    !evaluated.dashboard.share_enabled,
                  )
                }
              >
                {evaluated.dashboard.share_enabled
                  ? "Disable Share Link"
                  : "Enable Share Link"}
              </button>
              <a
                className="btn"
                data-testid="export-csv-link"
                href={`${BASE_URL}/api/v1/analytics/dashboards/${encodeURIComponent(
                  evaluated.dashboard.id,
                )}/export.csv`}
              >
                Export CSV
              </a>
            </div>
          </div>

          {evaluated.dashboard.share_enabled && evaluated.dashboard.share_url ? (
            <p data-testid="share-url-banner">
              Shareable link: <code>{evaluated.dashboard.share_url}</code>
            </p>
          ) : null}

          <dl className="kv" style={{ marginTop: "1rem" }}>
            <div>
              <dt>Total Calls</dt>
              <dd>{evaluated.summary.total_calls}</dd>
            </div>
            <div>
              <dt>Success Rate</dt>
              <dd>{evaluated.summary.success_rate}%</dd>
            </div>
            <div>
              <dt>Avg / P95 Duration</dt>
              <dd>
                {evaluated.summary.avg_duration_seconds}s /{" "}
                {evaluated.summary.p95_duration_seconds}s
              </dd>
            </div>
            <div>
              <dt>Cost / Call</dt>
              <dd>${evaluated.summary.avg_cost_per_call_usd}</dd>
            </div>
            <div>
              <dt>Latency P50 / P95</dt>
              <dd>
                {evaluated.summary.latency_p50_ms}ms /{" "}
                {evaluated.summary.latency_p95_ms}ms
              </dd>
            </div>
            <div>
              <dt>Transfer / Voicemail Rate</dt>
              <dd>
                {evaluated.summary.transfer_rate}% /{" "}
                {evaluated.summary.voicemail_rate}%
              </dd>
            </div>
          </dl>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "1rem",
              marginTop: "1rem",
            }}
          >
            {evaluated.widgets.map((w, idx) => (
              <div key={w.id ?? idx} className="card" data-testid="evaluated-widget">
                <h3>{w.title}</h3>
                <p className="muted">
                  {w.metric} · grouped by {w.dimension} ({w.chart_type})
                </p>
                {w.series.length === 0 ? (
                  <p className="empty">No data in selected window.</p>
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{w.dimension}</th>
                        <th>{w.metric}</th>
                        <th>Calls</th>
                      </tr>
                    </thead>
                    <tbody>
                      {w.series.map((pt) => (
                        <tr key={pt.key}>
                          <td>{pt.label}</td>
                          <td>
                            {typeof pt.value === "object"
                              ? JSON.stringify(pt.value)
                              : String(pt.value)}
                          </td>
                          <td>{pt.total_calls}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
