"use client";

import { useEffect, useState } from "react";

import { Callout, Empty, Failure, Loading, Metric, Panel, StatusPill } from "./components/Primitives";
import { plat2Api } from "./lib/api";
import type { AccuracyResponse, HistoryCounts, ProviderStatus } from "./lib/api";
import type { CustomDomainRow } from "./lib/api";
import {
  domainStatusLabel,
  domainTone,
  formatPercent,
  formatSignedPercent,
  worstDomainStatus,
} from "./lib/format";
import { useApi } from "./lib/useApi";
import styles from "./platform-admin.module.css";

/**
 * Overview: one screen that answers "is this tenant's platform healthy".
 *
 * Every number is read from the API; nothing is computed here that the backend already
 * owns (accuracy in particular is stored per scored forecast, not recomputed in the
 * browser), so the console and an export can never disagree.
 */
export default function PlatformAdminOverview() {
  const history = useApi<HistoryCounts>(() => plat2Api.historyCounts({}), []);
  const accuracy = useApi<AccuracyResponse>(() => plat2Api.readAccuracy({ limit: 10 }), []);
  const domains = useApi<{ domains: CustomDomainRow[]; count: number }>(
    () => plat2Api.listDomains(),
    [],
  );
  const providers = useApi<ProviderStatus>(() => plat2Api.providerStatus(), []);
  const [now, setNow] = useState<string | null>(null);

  // Rendered after mount so the server-rendered HTML and the client's first paint agree.
  useEffect(() => {
    setNow(new Date().toISOString());
  }, []);

  const domainRows = domains.data?.domains ?? [];
  const domainVerdict = worstDomainStatus(domainRows.map((row) => row.status));
  // Null (not `{}`) until the response lands, so a missing trend is visible in the
  // types instead of silently type-checking as an empty object.
  const trend = accuracy.data?.trend ?? null;
  // `samples === 0` means nothing has been scored yet. The backend sends zeroed
  // metrics in that case, and rendering them would report 0.0% error as if it had
  // been measured.
  const hasAccuracy = trend !== null && trend.samples > 0;

  return (
    <>
      <h1 className={styles.pageTitle}>Platform overview</h1>
      <p className={styles.pageLead}>
        Workforce, white-label and mobile surfaces for the current tenant. Counts come
        from stored interval history and scored forecasts; where the platform has not
        measured something yet, the panel says so instead of showing a zero.
      </p>

      {history.error ? (
        <Callout tone="bad" role="alert">
          History unavailable, so these counts are unknown rather than zero: {history.error.message}
        </Callout>
      ) : null}

      <div className={styles.grid}>
        <Metric
          label="Stored intervals"
          value={history.data ? history.data.intervals.toLocaleString("en-GB") : "—"}
          hint={
            history.data
              ? `${history.data.days.toLocaleString("en-GB")} days of history`
              : "Loading stored history"
          }
        />
        <Metric
          label="Forecast readiness"
          value={history.data ? (history.data.enough ? "Ready" : "Not yet") : "—"}
          hint={
            history.data
              ? `Needs ${history.data.required_days} days; the window runs ${
                  history.data.first ?? "—"
                } to ${history.data.last ?? "—"}`
              : "Loading stored history"
          }
        />
        <Metric
          label="Accuracy (WAPE, latest)"
          value={hasAccuracy && trend ? formatPercent(trend.last_wape) : "—"}
          hint={
            hasAccuracy && trend
              ? `Mean ${formatPercent(trend.mean_wape)} across ${trend.samples} scored forecast(s) · ${
                  trend.direction
                }`
              : "No scored forecast yet — the number appears after the first score run"
          }
        />
        <Metric
          label="Custom domains"
          value={domains.data ? String(domains.data.count) : "—"}
          hint={
            domainVerdict === "none"
              ? "No domain claimed for this tenant"
              : domainVerdict === "failed"
                ? "At least one domain failed verification"
                : domainVerdict === "active"
                  ? "All claimed domains are live"
                  : "A domain is still being verified"
          }
        />
      </div>

      <div className={styles.split}>
        <Panel
          title="Domain status"
          subtitle="Only a live domain is served with a certificate; the TLS ask refuses everything else."
          actions={
            <StatusPill
              tone={
                domainVerdict === "active"
                  ? "ok"
                  : domainVerdict === "failed"
                    ? "bad"
                    : domainVerdict === "none"
                      ? "neutral"
                      : "warn"
              }
              label={
                domainVerdict === "none"
                  ? "None claimed"
                  : domainVerdict === "active"
                    ? "All live"
                    : domainVerdict === "failed"
                      ? "Failure"
                      : "In progress"
              }
            />
          }
        >
          {domains.loading ? <Loading what="domains" /> : null}
          {domains.error ? (
            <Failure what="domains" error={domains.error} onRetry={domains.reload} />
          ) : null}
          {domains.data && domainRows.length === 0 ? (
            <Empty>No custom domain has been claimed yet.</Empty>
          ) : null}
          {domainRows.length > 0 ? (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Hostname</th>
                  <th>Status</th>
                  <th>Checked</th>
                </tr>
              </thead>
              <tbody>
                {domainRows.map((row) => (
                  <tr key={row.id}>
                    <td className={styles.mono}>{row.domain}</td>
                    <td>
                      <StatusPill tone={domainTone(row.status)} label={domainStatusLabel(row.status)} />
                    </td>
                    <td>{row.last_checked_at ? new Date(row.last_checked_at).toISOString().slice(0, 16).replace("T", " ") : "Never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </Panel>

        <Panel
          title="Push providers"
          subtitle="Configured means the credentials are present in this deployment, not that a message was delivered."
        >
          {providers.loading ? <Loading what="provider status" /> : null}
          {providers.error ? (
            <Failure what="provider status" error={providers.error} onRetry={providers.reload} />
          ) : null}
          {providers.data ? (
            <ul className={styles.checklist}>
              <li>
                <StatusPill
                  tone={providers.data.fcm.configured ? "ok" : "warn"}
                  label={providers.data.fcm.configured ? "FCM configured" : "FCM not configured"}
                />
                <span>Android delivery</span>
              </li>
              <li>
                <StatusPill
                  tone={providers.data.apns.configured ? "ok" : "warn"}
                  label={providers.data.apns.configured ? "APNs configured" : "APNs not configured"}
                />
                <span>
                  iOS delivery{providers.data.apns.configured
                    ? providers.data.apns.use_sandbox
                      ? " (sandbox host)"
                      : " (production host)"
                    : ""}
                </span>
              </li>
            </ul>
          ) : null}
          <Callout tone="info">
            A delivery row records what the provider answered. The console never renders
            a tick that claims a notification was seen on a device.
          </Callout>
        </Panel>
      </div>

      <Panel title="Forecast accuracy trend" subtitle="Stored per scored forecast, oldest to newest.">
        {accuracy.loading ? <Loading what="accuracy" /> : null}
        {accuracy.error ? (
          <Failure what="accuracy" error={accuracy.error} onRetry={accuracy.reload} />
        ) : null}
        {accuracy.data && accuracy.data.evaluations.length === 0 ? (
          <Empty>
            No forecast has been scored yet. Accuracy appears here after the scoring job
            runs against stored interval history.
          </Empty>
        ) : null}
        {accuracy.data && accuracy.data.evaluations.length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Evaluated to</th>
                <th>WAPE</th>
                <th>MAPE</th>
                <th>Bias</th>
                <th>MAE</th>
                <th>Samples</th>
              </tr>
            </thead>
            <tbody>
              {accuracy.data.evaluations.map((row) => (
                <tr key={`${row.forecast_id}-${row.evaluated_to}`}>
                  <td>{row.evaluated_to.slice(0, 10)}</td>
                  <td className={styles.numeric}>{formatPercent(row.wape)}</td>
                  <td className={styles.numeric}>{formatPercent(row.mape)}</td>
                  <td className={styles.numeric}>{formatSignedPercent(row.bias)}</td>
                  <td className={styles.numeric}>{row.mae.toFixed(2)}</td>
                  <td className={styles.numeric}>{row.sample_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>

      <p className={styles.state}>
        Snapshot rendered {now ? now.slice(0, 16).replace("T", " ") : "…"} UTC from the
        live API.
      </p>
    </>
  );
}
