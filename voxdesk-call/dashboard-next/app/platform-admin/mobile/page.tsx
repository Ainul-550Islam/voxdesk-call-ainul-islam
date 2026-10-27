"use client";

import { useState } from "react";

import { Callout, Empty, Failure, Loading, Metric, Panel, StatusPill } from "../components/Primitives";
import { plat2Api } from "../lib/api";
import type { MobileDeviceRow, ProviderStatus, PushDeliveryRow, TestPushResponse } from "../lib/api";
import {
  deliverySummaryLabel,
  deliveryTone,
  quietHoursLabel,
  timestampLabel,
} from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "../platform-admin.module.css";

/**
 * Mobile push: the caller's own devices, the deliveries the platform recorded, and a
 * test send.
 *
 * Everything on this page is scoped to the signed-in person by the API — there is no
 * user picker, because one agent enumerating another agent's phones is exactly the
 * access the routes refuse to grant.
 */
export default function MobilePage() {
  const devices = useApi<{ devices: MobileDeviceRow[]; count: number }>(
    () => plat2Api.listDevices(),
    [],
  );
  const deliveries = useApi<{ deliveries: PushDeliveryRow[]; summary: Record<string, number> }>(
    () => plat2Api.listDeliveries(25),
    [],
  );
  const providers = useApi<ProviderStatus>(() => plat2Api.providerStatus(), []);

  const [urgent, setUrgent] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<TestPushResponse | null>(null);

  const rows = devices.data?.devices ?? [];
  const activeTokens = rows.reduce(
    (total, device) => total + device.tokens.filter((token) => token.active).length,
    0,
  );

  async function sendTest() {
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const result = await plat2Api.sendTestPush({
        template: "push_test",
        title: "VoxDesk test notification",
        body: "If you can read this, push is working on this device.",
        urgent,
      });
      setOutcome(result);
      deliveries.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className={styles.pageTitle}>Mobile push</h1>
      <p className={styles.pageLead}>
        Devices registered to you, the delivery attempts the platform logged, and a test
        send. A dropped attempt names its reason — quiet hours, push disabled, no active
        token, revoked device, provider not configured — and no screen here claims a
        notification was read.
      </p>

      <div className={styles.grid}>
        <Metric label="My devices" value={String(rows.length)} hint="Registered to you" />
        <Metric label="Active tokens" value={String(activeTokens)} hint="Usable for delivery" />
        <Metric
          label="Providers"
          value={
            providers.data
              ? `${providers.data.fcm.configured ? "FCM" : ""}${
                  providers.data.fcm.configured && providers.data.apns.configured ? " + " : ""
                }${providers.data.apns.configured ? "APNs" : ""}` || "None"
              : "—"
          }
          hint="Configured in this deployment"
        />
      </div>

      <Panel
        title="Test delivery"
        subtitle="Urgent bypasses quiet hours, which is what makes a 23:00 test meaningful."
      >
        <div className={styles.inlineForm}>
          <label className={styles.field}>
            Bypass quiet hours
            <select
              value={urgent ? "yes" : "no"}
              onChange={(event) => setUrgent(event.target.value === "yes")}
            >
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </label>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            onClick={sendTest}
            disabled={busy}
          >
            {busy ? "Sending…" : "Send test push"}
          </button>
        </div>
        {error ? <Callout tone="bad">{error}</Callout> : null}
        {outcome ? (
          <>
            <ul className={styles.checklist}>
              {outcome.attempts.map((attempt, index) => (
                <li key={index}>
                  <StatusPill tone={deliveryTone(attempt.status)} label={attempt.status} />
                  <span>
                    {attempt.platform ?? "device"}
                    {attempt.reason ? ` — ${attempt.reason}` : ""}
                  </span>
                </li>
              ))}
            </ul>
            <Callout tone="info">{outcome.note}</Callout>
          </>
        ) : null}
      </Panel>

      <Panel title="My devices" subtitle="Tokens are shown as a hint only; the secret never leaves the server.">
        {devices.loading ? <Loading what="devices" /> : null}
        {devices.error ? <Failure what="devices" error={devices.error} onRetry={devices.reload} /> : null}
        {devices.data && rows.length === 0 ? (
          <Empty>No phone is registered to you. Sign in from the mobile app to register one.</Empty>
        ) : null}
        {rows.length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Platform</th>
                <th>App</th>
                <th>OS</th>
                <th>Push</th>
                <th>Quiet hours</th>
                <th>Tokens</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((device) => (
                <tr key={device.id}>
                  <td>{device.platform}</td>
                  <td className={styles.mono}>{device.app_version || "—"}</td>
                  <td className={styles.mono}>{device.os_version || "—"}</td>
                  <td>
                    <StatusPill
                      tone={device.revoked_at ? "neutral" : device.push_enabled ? "ok" : "warn"}
                      label={device.revoked_at ? "Revoked" : device.push_enabled ? "Enabled" : "Disabled"}
                    />
                  </td>
                  <td>{quietHoursLabel(device.quiet_hours)}</td>
                  <td className={styles.numeric}>
                    {device.tokens.filter((token) => token.active).length}/{device.tokens.length}
                  </td>
                  <td>{device.last_seen_at ? timestampLabel(device.last_seen_at) : "Never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>

      <Panel
        title="Recent deliveries"
        subtitle={deliverySummaryLabel(deliveries.data?.summary ?? null)}
      >
        {deliveries.loading ? <Loading what="deliveries" /> : null}
        {deliveries.error ? (
          <Failure what="deliveries" error={deliveries.error} onRetry={deliveries.reload} />
        ) : null}
        {deliveries.data && deliveries.data.deliveries.length === 0 ? (
          <Empty>No delivery has been attempted for you yet.</Empty>
        ) : null}
        {(deliveries.data?.deliveries ?? []).length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Status</th>
                <th>Provider</th>
                <th>Template</th>
                <th>Error</th>
                <th>Created</th>
                <th>Sent</th>
              </tr>
            </thead>
            <tbody>
              {(deliveries.data?.deliveries ?? []).map((delivery) => (
                <tr key={delivery.id}>
                  <td>
                    <StatusPill tone={deliveryTone(delivery.status)} label={delivery.status} />
                  </td>
                  <td>{delivery.provider}</td>
                  <td className={styles.mono}>{delivery.template}</td>
                  <td className={styles.mono}>
                    {delivery.error_reason || delivery.error_code || "—"}
                  </td>
                  <td>{delivery.created_at ? timestampLabel(delivery.created_at) : "—"}</td>
                  <td>{delivery.sent_at ? timestampLabel(delivery.sent_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>
    </>
  );
}
