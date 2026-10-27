"use client";

import { useState } from "react";

import { DailyVolumeBars } from "../components/DailyVolumeBars";
import { Callout, Empty, Failure, Loading, Metric, Panel, StatusPill } from "../components/Primitives";
import { plat2Api } from "../lib/api";
import type { ForecastSummary, LaborRuleRow, ShiftTemplateRow, StaffingPlan } from "../lib/api";
import { clockFromMinute, durationLabel, formatNumber, formatPercent, paidMinutes } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "../platform-admin.module.css";

/**
 * Forecast and staffing.
 *
 * The page is a two-step judgement, and it is laid out as one: pick a forecast (or make
 * one from stored history), then translate it into bodies using Erlang C with an
 * explicit service level, occupancy cap and shrinkage. Those three inputs are shown on
 * screen because a staffing number without them is not reproducible.
 */
export default function WorkforcePage() {
  const forecasts = useApi<{ forecasts: ForecastSummary[] }>(
    () => plat2Api.listForecasts({ limit: 12 }),
    [],
  );
  const templates = useApi<{ templates: ShiftTemplateRow[] }>(() => plat2Api.listTemplates(), []);
  const rules = useApi<{ rules: LaborRuleRow[] }>(() => plat2Api.listLaborRules(), []);

  const [queueKey, setQueueKey] = useState("");
  const [horizon, setHorizon] = useState(14);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const [serviceLevel, setServiceLevel] = useState(0.8);
  const [answerSeconds, setAnswerSeconds] = useState(20);
  const [maxOccupancy, setMaxOccupancy] = useState(0.85);
  const [shrinkage, setShrinkage] = useState(0.3);

  const staffing = useApi<StaffingPlan | null>(
    () =>
      selected
        ? plat2Api.readStaffing(selected, {
            serviceLevel,
            answerSeconds,
            maxOccupancy,
            shrinkage,
          })
        : Promise.resolve(null),
    [selected, serviceLevel, answerSeconds, maxOccupancy, shrinkage],
  );

  const rows = forecasts.data?.forecasts ?? [];
  const activeRule = (rules.data?.rules ?? []).find((rule) => rule.is_active) ?? null;

  async function createForecast() {
    setBusy(true);
    setActionError(null);
    try {
      const created = await plat2Api.createForecast({
        queue_key: queueKey,
        horizon_days: horizon,
      });
      setSelected(created.id);
      forecasts.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className={styles.pageTitle}>Forecast &amp; staffing</h1>
      <p className={styles.pageLead}>
        Forecasts are fitted from stored interval history and scored against what actually
        happened; staffing is Erlang C on the forecast you select, with the occupancy cap
        and shrinkage applied before headcount is reported.
      </p>

      <Panel
        title="Generate a forecast"
        subtitle="Leave the queue key empty for the environment-wide total. Horizon is capped at 90 days."
      >
        <div className={styles.inlineForm}>
          <label className={styles.field}>
            Queue key
            <input
              value={queueKey}
              onChange={(event) => setQueueKey(event.target.value)}
              placeholder="(environment total)"
              maxLength={36}
            />
          </label>
          <label className={styles.field}>
            Horizon (days)
            <input
              type="number"
              min={1}
              max={90}
              value={horizon}
              onChange={(event) => setHorizon(Number(event.target.value))}
            />
          </label>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            onClick={createForecast}
            disabled={busy}
          >
            {busy ? "Fitting…" : "Fit from history"}
          </button>
        </div>
        {actionError ? <Callout tone="bad">{actionError}</Callout> : null}
        <Callout tone="info">
          Fitting needs at least three weeks of history for the seasonal model; shorter
          history falls back to the additive method, and the forecast row says which
          method produced it.
        </Callout>
      </Panel>

      <Panel title="Forecasts" subtitle="Most recent first. Select one to plan staffing against it.">
        {forecasts.loading ? <Loading what="forecasts" /> : null}
        {forecasts.error ? (
          <Failure what="forecasts" error={forecasts.error} onRetry={forecasts.reload} />
        ) : null}
        {forecasts.data && rows.length === 0 ? (
          <Empty>No forecast stored yet for this tenant.</Empty>
        ) : null}
        {rows.length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Generated</th>
                <th>Queue</th>
                <th>Method</th>
                <th>Horizon</th>
                <th>History</th>
                <th>Total</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.generated_at.slice(0, 16).replace("T", " ")}</td>
                  <td className={styles.mono}>{row.queue_key || "(total)"}</td>
                  <td>
                    {row.method} <span className={styles.mono}>{row.model_version}</span>
                  </td>
                  <td className={styles.numeric}>{row.horizon_days} d</td>
                  <td className={styles.numeric}>{row.history_days} d</td>
                  <td className={styles.numeric}>{formatNumber(row.total_expected ?? 0, 0)}</td>
                  <td>
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => setSelected(row.id)}
                    >
                      {selected === row.id ? "Selected" : "Plan staffing"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>

      {selected ? (
        <Panel
          title="Staffing requirement"
          subtitle="Erlang C with abandonment-free queueing; occupancy caps the utilisation used to size headcount."
        >
          <div className={styles.inlineForm}>
            <label className={styles.field}>
              Service level
              <input
                type="number"
                step={0.01}
                min={0.5}
                max={0.99}
                value={serviceLevel}
                onChange={(event) => setServiceLevel(Number(event.target.value))}
              />
            </label>
            <label className={styles.field}>
              Answer within (s)
              <input
                type="number"
                min={5}
                max={120}
                value={answerSeconds}
                onChange={(event) => setAnswerSeconds(Number(event.target.value))}
              />
            </label>
            <label className={styles.field}>
              Max occupancy
              <input
                type="number"
                step={0.05}
                min={0.5}
                max={1}
                value={maxOccupancy}
                onChange={(event) => setMaxOccupancy(Number(event.target.value))}
              />
            </label>
            <label className={styles.field}>
              Shrinkage
              <input
                type="number"
                step={0.05}
                min={0}
                max={0.9}
                value={shrinkage}
                onChange={(event) => setShrinkage(Number(event.target.value))}
              />
            </label>
          </div>

          {staffing.loading ? <Loading what="staffing plan" /> : null}
          {staffing.error ? (
            <Failure what="staffing plan" error={staffing.error} onRetry={staffing.reload} />
          ) : null}
          {staffing.data ? (
            <>
              <div className={styles.grid}>
                <Metric
                  label="Peak agents (paid)"
                  value={formatNumber(staffing.data.peak_paid_agents, 0)}
                  hint={`median ${formatNumber(staffing.data.distribution.median, 0)} · p90 ${formatNumber(
                    staffing.data.distribution.p90,
                    0,
                  )}`}
                />
                <Metric
                  label="Paid hours"
                  value={formatNumber(staffing.data.paid_hours, 1)}
                  hint={`shrinkage ${formatPercent(staffing.data.target.shrinkage, 0)} applied`}
                />
                <Metric
                  label="AHT used"
                  value={`${formatNumber(staffing.data.target.average_handle_seconds, 0)} s`}
                  hint={`SL ${formatPercent(staffing.data.target.service_level, 0)} in ${staffing.data.target.answer_seconds}s`}
                />
                <Metric
                  label="Occupancy cap"
                  value={formatPercent(staffing.data.target.max_occupancy, 0)}
                  hint={`target band ${staffing.data.band}`}
                />
              </div>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Peak agents</th>
                    <th>Paid hours</th>
                    <th>Volume</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(staffing.data.daily).map(([day, values]) => (
                    <tr key={day}>
                      <td>{day}</td>
                      <td className={styles.numeric}>{formatNumber(values.paid_agents_peak, 0)}</td>
                      <td className={styles.numeric}>{formatNumber(values.paid_hours, 1)}</td>
                      <td className={styles.numeric}>{formatNumber(values.volume, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </Panel>
      ) : null}

      <div className={styles.split}>
        <Panel title="Shift templates" subtitle="Paid time is length minus the unpaid break.">
          {templates.loading ? <Loading what="templates" /> : null}
          {templates.error ? (
            <Failure what="templates" error={templates.error} onRetry={templates.reload} />
          ) : null}
          {templates.data && templates.data.templates.length === 0 ? (
            <Empty>No shift template defined yet.</Empty>
          ) : null}
          {(templates.data?.templates ?? []).length > 0 ? (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Start</th>
                  <th>Length</th>
                  <th>Paid</th>
                  <th>Skill</th>
                  <th>Over cap</th>
                </tr>
              </thead>
              <tbody>
                {(templates.data?.templates ?? []).map((template) => {
                  const paid = paidMinutes(template);
                  const overCap =
                    activeRule !== null && paid > activeRule.max_shift_hours * 60;
                  return (
                    <tr key={template.id}>
                      <td>{template.name}</td>
                      <td className={styles.numeric}>{clockFromMinute(template.start_minute)}</td>
                      <td className={styles.numeric}>{durationLabel(template.length_minutes)}</td>
                      <td className={styles.numeric}>{durationLabel(paid)}</td>
                      <td>{template.required_skill_key || "—"}</td>
                      <td>
                        {overCap ? (
                          <StatusPill tone="bad" label="Over shift cap" />
                        ) : (
                          <StatusPill tone="ok" label="Within cap" />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : null}
        </Panel>

        <Panel title="Labor rules" subtitle="The constraint set the solver enforces.">
          {rules.loading ? <Loading what="labor rules" /> : null}
          {rules.error ? (
            <Failure what="labor rules" error={rules.error} onRetry={rules.reload} />
          ) : null}
          {rules.data && rules.data.rules.length === 0 ? (
            <Empty>No labor rule set; the solver falls back to its built-in defaults.</Empty>
          ) : null}
          {(rules.data?.rules ?? []).length > 0 ? (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Max h/week</th>
                  <th>Rest</th>
                  <th>Consecutive days</th>
                  <th>Max shift</th>
                </tr>
              </thead>
              <tbody>
                {(rules.data?.rules ?? []).map((rule) => (
                  <tr key={rule.id}>
                    <td>
                      {rule.name} {rule.is_active ? <StatusPill tone="ok" label="Active" /> : null}
                    </td>
                    <td className={styles.numeric}>{formatNumber(rule.max_hours_per_week, 1)}</td>
                    <td className={styles.numeric}>{durationLabel(rule.min_rest_hours * 60)}</td>
                    <td className={styles.numeric}>{rule.max_consecutive_days}</td>
                    <td className={styles.numeric}>{durationLabel(rule.max_shift_hours * 60)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </Panel>
      </div>

      {selected ? (
        <Panel title="Daily expectation" subtitle="What the selected forecast expects, day by day.">
          <DailyVolumeBars
            totals={rows.find((row) => row.id === selected)?.daily_totals ?? {}}
          />
        </Panel>
      ) : null}
    </>
  );
}
