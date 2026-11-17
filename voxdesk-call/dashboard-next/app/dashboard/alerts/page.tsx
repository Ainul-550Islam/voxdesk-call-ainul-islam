// File: dashboard-next/app/dashboard/alerts/page.tsx — alert rule builder, history and test-fire
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, request } from "@/lib/api";

/**
 * Alert rules (PART 15, P15-04).
 *
 * Everything on this page comes from `/api/alerts/*` (app/api/alert_rule_routes.py):
 *   GET    /api/alerts/rules                 the rules, the metric list and the comparators
 *   POST   /api/alerts/rules                 create
 *   PATCH  /api/alerts/rules/{id}            enable, disable, retune
 *   DELETE /api/alerts/rules/{id}            remove
 *   POST   /api/alerts/rules/{id}/test-fire  dry evaluation (writes nothing unless deliver=true)
 *   GET    /api/alerts/events                what actually fired or was suppressed
 *   POST   /api/alerts/evaluate?deliver=     evaluate every rule now
 *
 * Three deliberate choices, all about honesty:
 *   * the test button is a *dry run*: `deliver: false`, and the result line says
 *     "not recorded" because a test that pages the on-call engineer is a test
 *     nobody presses twice;
 *   * the history lists suppressed events (`suppressed_cooldown`,
 *     `suppressed_dedupe`) next to fired ones, so a rule that is being muted by
 *     its own cooldown is visible instead of looking healthy;
 *   * "evaluate all now" calls the same endpoint the scheduler calls with
 *     `deliver=false`, so pressing it cannot post to Slack twice.
 */

interface AlertRule {
  id: string;
  name: string;
  metric: string;
  comparator: string;
  threshold: number;
  window_minutes: number;
  cooldown_minutes: number;
  scope_kind: string;
  scope_id: string | null;
  channels: Array<string | { channel: string; recipient?: string }>;
  enabled: boolean;
  description: string;
  updated_at: string | null;
}

interface AlertEvent {
  id: string;
  rule_id: string | null;
  metric: string;
  observed_value: number;
  threshold: number;
  state: string;
  deliveries: Array<{ channel: string; status: string; detail?: string }>;
  created_at: string | null;
}

interface RulesResponse {
  rules: AlertRule[];
  metrics: string[];
  comparators: string[];
}

interface EventsResponse {
  events: AlertEvent[];
  hours: number;
}

interface TestFireResponse {
  rule_id: string;
  metric: string;
  observed: number | null;
  threshold: number;
  comparator: string;
  would_breach: boolean;
  sampled: boolean;
  recorded: boolean;
  delivered: Array<{ channel: string; status: string; detail?: string }>;
  state?: string;
}

const COMPARATOR_LABEL: Record<string, string> = {
  gt: "is above",
  gte: "is at or above",
  lt: "is below",
  lte: "is at or below",
};

/** The states the evaluator writes, each mapped to the colour a badge already has. */
const STATE_BADGE: Record<string, string> = {
  fired: "badge failed",
  delivery_failed: "badge failed",
  suppressed_cooldown: "badge paused",
  suppressed_dedupe: "badge paused",
};

const STATE_HELP: Record<string, string> = {
  fired: "breached and delivered",
  delivery_failed: "breached; at least one channel failed",
  suppressed_cooldown: "breached inside the rule's cooldown — recorded, not sent",
  suppressed_dedupe: "same breach already reported in this window bucket",
};

/**
 * Plain-language copy for each metric in `evaluator.METRICS`. A metric with no
 * entry here still works — the page just shows no explanation rather than a
 * guessed one.
 */
const METRIC_HELP: Record<string, string> = {
  cost_burn_per_minute: "Provider spend per minute, from metered usage.",
  success_rate: "Booked or completed calls as a share of answered calls.",
  sentiment_delta: "Change in mean caller sentiment against the previous window.",
  p95_latency: "95th percentile of per-call end-to-end latency, in seconds.",
  error_rate: "Failed calls as a share of all calls in the window.",
  concurrency_saturation: "Peak concurrent calls as a share of the ceiling.",
  consent_breach: "Calls recorded without consent on file.",
  anomaly_zscore: "Distance from the rolling 24 h baseline, in sigmas.",
};

const FIELD_STYLE = { display: "grid", gap: "0.75rem" } as const;

const GRID_STYLE = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: "0.75rem",
} as const;

function emptyDraft() {
  return {
    name: "",
    metric: "error_rate",
    comparator: "gt",
    threshold: "5",
    window_minutes: "15",
    cooldown_minutes: "30",
    scope_kind: "tenant",
    scope_id: "",
    channels: "in_app",
  };
}

export default function AlertsPage() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [metrics, setMetrics] = useState<string[]>([]);
  const [comparators, setComparators] = useState<string[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [eventHours, setEventHours] = useState<number>(48);
  const [draft, setDraft] = useState(emptyDraft());
  const [error, setError] = useState<string | null>(null);
  /**
   * Kept apart from `error`: a failed *load* means the tables below say nothing
   * about this tenant's rules, while a failed *action* leaves the last good list
   * on screen. Collapsing the two is how an empty table lies.
   */
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [tested, setTested] = useState<Record<string, TestFireResponse>>({});

  const loadRules = useCallback(async () => {
    const data = await request<RulesResponse>("/api/alerts/rules");
    setRules(data.rules ?? []);
    setMetrics(data.metrics ?? []);
    setComparators(data.comparators ?? []);
  }, []);

  const loadEvents = useCallback(async () => {
    const data = await request<EventsResponse>("/api/alerts/events?hours=48&limit=50");
    setEvents(data.events ?? []);
    if (typeof data.hours === "number") setEventHours(data.hours);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadRules(), loadEvents()])
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiError
            ? err.message
            : "Could not load alert rules. The tables below are not evidence of anything.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadRules, loadEvents]);

  const ruleLabel = useMemo(() => {
    const byId = new Map(rules.map((rule) => [rule.id, rule.name]));
    return (id: string | null) => (id ? byId.get(id) ?? "deleted rule" : "deleted rule");
  }, [rules]);

  async function run(action: () => Promise<void>) {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await action();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "The request failed.");
    } finally {
      setBusy(false);
    }
  }

  function createRule() {
    void run(async () => {
      const channels = draft.channels
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean);
      const payload: Record<string, unknown> = {
        name: draft.name.trim(),
        metric: draft.metric,
        comparator: draft.comparator,
        threshold: Number(draft.threshold),
        window_minutes: Number(draft.window_minutes),
        cooldown_minutes: Number(draft.cooldown_minutes),
        scope_kind: draft.scope_kind,
        scope_id: draft.scope_kind === "tenant" ? null : draft.scope_id.trim(),
        channels,
        enabled: true,
      };
      const created = await request<AlertRule>("/api/alerts/rules", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setDraft(emptyDraft());
      setNotice(`Created ${created.name}. ${created.description}`);
      await loadRules();
    });
  }

  function toggle(rule: AlertRule) {
    void run(async () => {
      const updated = await request<AlertRule>(
        `/api/alerts/rules/${encodeURIComponent(rule.id)}`,
        { method: "PATCH", body: JSON.stringify({ enabled: !rule.enabled }) },
      );
      setNotice(`${updated.name} is now ${updated.enabled ? "enabled" : "disabled"}.`);
      await loadRules();
    });
  }

  function remove(rule: AlertRule) {
    void run(async () => {
      await request(`/api/alerts/rules/${encodeURIComponent(rule.id)}`, {
        method: "DELETE",
      });
      setNotice(`Deleted ${rule.name}.`);
      setTested((prev) => {
        const next = { ...prev };
        delete next[rule.id];
        return next;
      });
      await loadRules();
    });
  }

  function testFire(rule: AlertRule) {
    void run(async () => {
      const result = await request<TestFireResponse>(
        `/api/alerts/rules/${encodeURIComponent(rule.id)}/test-fire`,
        { method: "POST", body: JSON.stringify({ deliver: false }) },
      );
      setTested((prev) => ({ ...prev, [rule.id]: result }));
    });
  }

  function evaluateNow() {
    void run(async () => {
      const result = await request<{ evaluated: number; breached: number; deliver: boolean }>(
        "/api/alerts/evaluate?deliver=false",
      );
      setNotice(
        `Evaluated ${result.evaluated} rule(s); ${result.breached} breached. ` +
          (result.deliver ? "Deliveries attempted." : "Dry run — nothing was sent."),
      );
      await loadEvents();
    });
  }

  return (
    <div data-testid="alerts-page">
      <header className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="muted">
            Rules are evaluated against stored metrics. A breach inside a cooldown is still recorded
            — the history below shows what was suppressed, not only what was sent. Test-fire is a
            dry run unless you ask for delivery.
          </p>
        </div>
      </header>

      {loadError ? (
        <div className="error-banner" role="alert" data-testid="alerts-load-error">
          Alert rules could not be loaded: {loadError} No rule state is shown below, because an
          empty table would look like "nothing is wrong".
        </div>
      ) : null}

      {error ? (
        <div className="error-banner" role="alert" data-testid="alerts-error">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="card" data-testid="alerts-notice">
          {notice}
        </div>
      ) : null}

      <section className="card" data-testid="alert-rule-form">
        <h2>New rule</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            createRule();
          }}
          style={FIELD_STYLE}
        >
          <div style={GRID_STYLE}>
            <label>
              Name
              <input
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="Error rate over 5%"
                data-testid="alert-rule-name"
              />
            </label>
            <label>
              Metric
              <select
                value={draft.metric}
                onChange={(event) => setDraft({ ...draft, metric: event.target.value })}
                data-testid="alert-rule-metric"
              >
                {metrics.map((metric) => (
                  <option key={metric} value={metric}>
                    {metric}
                  </option>
                ))}
              </select>
              <small className="muted">{METRIC_HELP[draft.metric] ?? ""}</small>
            </label>
            <label>
              Comparator
              <select
                value={draft.comparator}
                onChange={(event) => setDraft({ ...draft, comparator: event.target.value })}
              >
                {comparators.map((value) => (
                  <option key={value} value={value}>
                    {value} — {COMPARATOR_LABEL[value] ?? value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Threshold
              <input
                type="number"
                step="any"
                value={draft.threshold}
                onChange={(event) => setDraft({ ...draft, threshold: event.target.value })}
                data-testid="alert-rule-threshold"
              />
            </label>
            <label>
              Window (minutes)
              <input
                type="number"
                min={1}
                max={1440}
                value={draft.window_minutes}
                onChange={(event) =>
                  setDraft({ ...draft, window_minutes: event.target.value })
                }
              />
            </label>
            <label>
              Cooldown (minutes)
              <input
                type="number"
                min={0}
                max={1440}
                value={draft.cooldown_minutes}
                onChange={(event) =>
                  setDraft({ ...draft, cooldown_minutes: event.target.value })
                }
              />
            </label>
            <label>
              Scope
              <select
                value={draft.scope_kind}
                onChange={(event) => setDraft({ ...draft, scope_kind: event.target.value })}
              >
                <option value="tenant">Whole tenant</option>
                <option value="agent">One agent</option>
                <option value="number">One phone number</option>
              </select>
            </label>
            {draft.scope_kind !== "tenant" ? (
              <label>
                Scope id
                <input
                  value={draft.scope_id}
                  onChange={(event) => setDraft({ ...draft, scope_id: event.target.value })}
                  placeholder={draft.scope_kind === "agent" ? "agent id" : "+8801XXXXXXXXX"}
                />
              </label>
            ) : null}
            <label>
              Channels
              <input
                value={draft.channels}
                onChange={(event) => setDraft({ ...draft, channels: event.target.value })}
                placeholder="in_app, webhook"
              />
              <small className="muted">
                Comma separated channel names (at most 8). A channel without a recipient is
                reported as <code>skipped</code> — it is never silently sent.
              </small>
            </label>
          </div>

          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <button
              type="submit"
              className="btn primary"
              disabled={busy || draft.name.trim() === ""}
              data-testid="alert-rule-submit"
            >
              Create rule
            </button>
            <button
              type="button"
              className="btn"
              onClick={evaluateNow}
              disabled={busy}
              data-testid="alerts-evaluate-now"
            >
              Evaluate all now (dry run)
            </button>
          </div>
        </form>
      </section>

      <section className="card" data-testid="alert-rules-card">
        <h2>Rules</h2>
        {loading ? (
          <p className="muted">Loading rules…</p>
        ) : loadError ? (
          <p className="muted">Unavailable — see the error above.</p>
        ) : rules.length === 0 ? (
          <p className="muted">No rules yet. Nothing is being watched.</p>
        ) : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Data table">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Condition</th>
                  <th>Window</th>
                  <th>Cooldown</th>
                  <th>Scope</th>
                  <th>Channels</th>
                  <th>State</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => {
                  const result = tested[rule.id];
                  return (
                    <tr key={rule.id}>
                      <td>{rule.name}</td>
                      <td>{rule.description}</td>
                      <td>{rule.window_minutes}m</td>
                      <td>{rule.cooldown_minutes}m</td>
                      <td>
                        {rule.scope_kind === "tenant"
                          ? "tenant"
                          : `${rule.scope_kind}: ${rule.scope_id ?? "—"}`}
                      </td>
                      <td>
                        {rule.channels
                          .map((entry) => (typeof entry === "string" ? entry : entry.channel))
                          .join(", ") || "—"}
                      </td>
                      <td>
                        <span className={rule.enabled ? "badge completed" : "badge paused"}>
                          {rule.enabled ? "enabled" : "disabled"}
                        </span>
                        {result ? (
                          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                            dry run: observed {result.observed ?? "no data"} ·{" "}
                            {result.would_breach ? "would fire" : "would stay quiet"} ·{" "}
                            {result.recorded ? "recorded" : "not recorded"}
                            {result.sampled ? " · sampled from stored metrics" : ""}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => testFire(rule)}
                            disabled={busy}
                          >
                            Test
                          </button>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => toggle(rule)}
                            disabled={busy}
                          >
                            {rule.enabled ? "Disable" : "Enable"}
                          </button>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => remove(rule)}
                            disabled={busy}
                            data-testid={`alert-delete-${rule.id}`}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" data-testid="alert-history-card">
        <h2>History (last {eventHours} h)</h2>
        {loadError ? (
          <p className="muted">Unavailable — see the error above.</p>
        ) : events.length === 0 ? (
          <p className="muted">
            Nothing breached in this window. Suppressed breaches appear here too, so an empty list
            means the evaluator ran and every rule stayed quiet.
          </p>
        ) : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Data table">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Rule</th>
                  <th>Metric</th>
                  <th>Observed</th>
                  <th>State</th>
                  <th>Delivery</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id}>
                    <td>
                      {event.created_at
                        ? new Date(`${event.created_at}Z`).toLocaleString()
                        : "—"}
                    </td>
                    <td>{ruleLabel(event.rule_id)}</td>
                    <td>{event.metric}</td>
                    <td>
                      {event.observed_value} (threshold {event.threshold})
                    </td>
                    <td>
                      <span
                        className={STATE_BADGE[event.state] ?? "badge"}
                        title={STATE_HELP[event.state] ?? event.state}
                      >
                        {event.state}
                      </span>
                    </td>
                    <td>
                      {event.deliveries.length === 0
                        ? "—"
                        : event.deliveries
                            .map((item) => `${item.channel}: ${item.status}`)
                            .join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
