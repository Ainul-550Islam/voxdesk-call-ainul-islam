"use client";

import { useState } from "react";

import { Callout, Empty, Failure, Loading, Panel, StatusPill } from "../components/Primitives";
import { plat2Api } from "../lib/api";
import type { SenderDomainRow, SenderRecordRow, SenderRecordsMap } from "../lib/api";
import { timestampLabel } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "../platform-admin.module.css";

/**
 * Sender domains.
 *
 * Branded mail is the one place where a mistake reaches the customer's inbox, so the
 * page shows the three records separately: SPF, DKIM and DMARC. All three must verify —
 * DMARC especially, because a `p=none` policy alongside a passing SPF is still a domain
 * that anyone can spoof.
 */
export default function EmailDomainsPage() {
  const senders = useApi<{ sender_domains: SenderDomainRow[] }>(
    () => plat2Api.listSenderDomains(),
    [],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const records = useApi<{ records: SenderRecordsMap; checks: SenderRecordRow[] }>(
    () =>
      selectedId
        ? plat2Api.senderRecords(selectedId)
        : Promise.resolve({ records: {}, checks: [] }),
    [selectedId],
  );

  const rows = senders.data?.sender_domains ?? [];
  const selected = rows.find((row) => row.id === selectedId) ?? null;

  async function verify(senderId: string) {
    setBusy(senderId);
    setError(null);
    setResult(null);
    try {
      const outcome = await plat2Api.verifySender(senderId);
      setResult(outcome);
      senders.reload();
      records.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <h1 className={styles.pageTitle}>Sender domains</h1>
      <p className={styles.pageLead}>
        Each sender domain is verified against live DNS for SPF, DKIM and DMARC. A domain
        is usable for branded notifications only when all three pass; the panel below shows
        the observed value beside the expected one so a mismatch is diagnosable without
        opening a terminal.
      </p>

      <Panel title="Domains" subtitle="Verify re-reads DNS; nothing here is cached from a previous pass.">
        {senders.loading ? <Loading what="sender domains" /> : null}
        {senders.error ? (
          <Failure what="sender domains" error={senders.error} onRetry={senders.reload} />
        ) : null}
        {senders.data && rows.length === 0 ? (
          <Empty>No sender domain configured for this tenant.</Empty>
        ) : null}
        {error ? <Callout tone="bad">{error}</Callout> : null}
        {rows.length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Domain</th>
                <th>Status</th>
                <th>SPF</th>
                <th>DKIM</th>
                <th>DMARC</th>
                <th>Last checked</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className={styles.mono}>{row.domain}</td>
                  <td>
                    <StatusPill
                      tone={row.status === "verified" ? "ok" : "warn"}
                      label={row.status}
                    />
                  </td>
                  <td>
                    <StatusPill tone={row.spf_verified ? "ok" : "bad"} label={row.spf_verified ? "Pass" : "Fail"} />
                  </td>
                  <td>
                    <StatusPill tone={row.dkim_verified ? "ok" : "bad"} label={row.dkim_verified ? "Pass" : "Fail"} />
                  </td>
                  <td>
                    <StatusPill
                      tone={row.dmarc_verified ? "ok" : "bad"}
                      label={row.dmarc_verified ? "Pass" : "Fail"}
                    />
                  </td>
                  <td>{row.last_checked_at ? timestampLabel(row.last_checked_at) : "Never"}</td>
                  <td>
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => setSelectedId(row.id)}
                    >
                      {selectedId === row.id ? "Selected" : "Records"}
                    </button>{" "}
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => verify(row.id)}
                      disabled={busy !== null}
                    >
                      {busy === row.id ? "Checking…" : "Verify"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>

      {result ? (
        <Panel title="Verification outcome">
          <pre className={styles.mono} style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </Panel>
      ) : null}

      {selected ? (
        <Panel
          title={`Publish these for ${selected.domain}`}
          subtitle="What DNS must contain. Available immediately after the domain is added, before anything has been checked."
        >
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Type</th>
                <th>Name</th>
                <th>Value</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {(["spf", "dkim", "dmarc"] as const).map((key) => {
                const spec = records.data?.records[key] ?? selected.records[key];
                if (!spec) return null;
                return (
                  <tr key={key}>
                    <td>{spec.record_type}</td>
                    <td className={styles.mono}>{spec.name}</td>
                    <td className={styles.mono}>{spec.value}</td>
                    <td>{spec.purpose}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Callout tone="info">
            DMARC at <span className={styles.mono}>p=none</span> is reported as unverified:
            a monitor-only policy does not stop spoofing.
          </Callout>
        </Panel>
      ) : null}

      {selected ? (
        <Panel
          title={`Checks for ${selected.domain}`}
          subtitle="Expected versus observed, as read from DNS in the last verification."
        >
          {records.loading ? <Loading what="checks" /> : null}
          {records.error ? (
            <Failure what="checks" error={records.error} onRetry={records.reload} />
          ) : null}
          {records.data && records.data.checks.length === 0 ? (
            <Empty>No record has been checked yet — run Verify.</Empty>
          ) : null}
          {(records.data?.checks ?? []).length > 0 ? (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Name</th>
                  <th>Expected</th>
                  <th>Observed</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {(records.data?.checks ?? []).map((record) => (
                  <tr key={record.id}>
                    <td>{record.record_type}</td>
                    <td className={styles.mono}>{record.name}</td>
                    <td className={styles.mono}>{record.expected}</td>
                    <td className={styles.mono}>
                      {(record.observed ?? []).length ? record.observed.join(" ; ") : "(nothing)"}
                    </td>
                    <td>
                      <StatusPill tone={record.passed ? "ok" : "bad"} label={record.passed ? "Pass" : "Fail"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </Panel>
      ) : null}
    </>
  );
}
