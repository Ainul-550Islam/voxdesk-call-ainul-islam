"use client";

import { useState } from "react";

import { Callout, Empty, Failure, Loading, Metric, Panel, StatusPill } from "../components/Primitives";
import { plat2Api } from "../lib/api";
import type { CertificateStatus, CustomDomainRow, DomainCheckRow, DomainVerificationResult } from "../lib/api";
import {
  certificateTone,
  domainStatusLabel,
  domainTone,
  timestampLabel,
  worstDomainStatus,
} from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "../platform-admin.module.css";

/**
 * Custom domains.
 *
 * The flow is claim → publish DNS → verify → certificate. Each step is shown with the
 * record the operator has to create, and the ownership token is displayed once, at
 * claim time, because the API never returns it again.
 */
export default function DomainsPage() {
  const domains = useApi<{ domains: CustomDomainRow[]; count: number }>(
    () => plat2Api.listDomains(),
    [],
  );
  const [hostname, setHostname] = useState("");
  const [claimedToken, setClaimedToken] = useState<{ domain: string; token: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [verification, setVerification] = useState<DomainVerificationResult | null>(null);

  const checks = useApi<{ checks: DomainCheckRow[] }>(
    () => (selectedId ? plat2Api.domainChecks(selectedId) : Promise.resolve({ checks: [] })),
    [selectedId],
  );
  const certificate = useApi<CertificateStatus | null>(
    () => (selectedId ? plat2Api.readCertificate(selectedId) : Promise.resolve(null)),
    [selectedId],
  );

  const rows = domains.data?.domains ?? [];
  const selected = rows.find((row) => row.id === selectedId) ?? null;
  const verdict = worstDomainStatus(rows.map((row) => row.status));

  async function claim() {
    setBusy("claim");
    setError(null);
    setClaimedToken(null);
    try {
      const created = await plat2Api.createDomain({ domain: hostname });
      setClaimedToken({ domain: created.domain, token: created.ownership_token });
      setHostname("");
      domains.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function verify(domainId: string) {
    setBusy(domainId);
    setError(null);
    setVerification(null);
    try {
      const result = await plat2Api.verifyDomain(domainId);
      setVerification(result);
      domains.reload();
      checks.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <h1 className={styles.pageTitle}>Custom domains</h1>
      <p className={styles.pageLead}>
        A white-label hostname is only served once the ownership TXT record and the routing
        CNAME both resolve consistently, and a certificate has been issued. Until then the
        on-demand TLS ask refuses the name, so a stranger cannot point DNS at this platform
        and make it mint certificates.
      </p>

      <div className={styles.grid}>
        <Metric label="Claimed" value={String(rows.length)} hint="Hostnames on this tenant" />
        <Metric
          label="Live"
          value={String(rows.filter((row) => row.status === "active").length)}
          hint="Served with a certificate"
        />
        <Metric
          label="Attention"
          value={verdict === "failed" ? "Yes" : "No"}
          hint="Failed verification or expired certificate"
        />
      </div>

      <Panel title="Claim a hostname" subtitle="The platform's own hostnames cannot be claimed.">
        <div className={styles.inlineForm}>
          <label className={styles.field}>
            Hostname
            <input
              value={hostname}
              onChange={(event) => setHostname(event.target.value)}
              placeholder="support.example.com"
              maxLength={253}
            />
          </label>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            onClick={claim}
            disabled={busy !== null || hostname.trim().length < 3}
          >
            {busy === "claim" ? "Claiming…" : "Claim"}
          </button>
        </div>
        {error ? <Callout tone="bad">{error}</Callout> : null}
        {claimedToken ? (
          <Callout tone="warn">
            Ownership token for <strong>{claimedToken.domain}</strong>:{" "}
            <span className={styles.mono}>{claimedToken.token}</span>
            <br />
            This value is shown once. Create the TXT record now; the token is not returned
            again by any endpoint.
          </Callout>
        ) : null}
      </Panel>

      <Panel title="Domains" subtitle="Select a hostname to see its DNS checks and certificate state.">
        {domains.loading ? <Loading what="domains" /> : null}
        {domains.error ? (
          <Failure what="domains" error={domains.error} onRetry={domains.reload} />
        ) : null}
        {domains.data && rows.length === 0 ? <Empty>No hostname claimed yet.</Empty> : null}
        {rows.length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Hostname</th>
                <th>Status</th>
                <th>Verified</th>
                <th>Reverify due</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className={styles.mono}>{row.domain}</td>
                  <td>
                    <StatusPill tone={domainTone(row.status)} label={domainStatusLabel(row.status)} />
                    {row.failure_reason ? (
                      <div className={styles.metricHint}>{row.failure_reason}</div>
                    ) : null}
                  </td>
                  <td>{row.verified_at ? timestampLabel(row.verified_at) : "Never"}</td>
                  <td>{row.reverify_due_at ? timestampLabel(row.reverify_due_at) : "—"}</td>
                  <td>
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => {
                        setSelectedId(row.id);
                        setVerification(null);
                      }}
                    >
                      {selectedId === row.id ? "Selected" : "Details"}
                    </button>{" "}
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => verify(row.id)}
                      disabled={busy !== null}
                    >
                      {busy === row.id ? "Checking DNS…" : "Verify now"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>

      {verification ? (
        <Panel title="Verification result" subtitle={`Attempt for ${verification.domain}`}>
          <Callout tone={verification.passed ? "ok" : "bad"}>
            {verification.passed
              ? `Both records resolve consistently; status is now ${verification.status}.`
              : verification.reason || "Verification did not pass."}
          </Callout>
          <ul className={styles.checklist}>
            {verification.checks.map((check, index) => (
              <li key={index}>
                <StatusPill
                  tone={String(check.passed) === "true" ? "ok" : "bad"}
                  label={String(check.check_type ?? `check ${index + 1}`)}
                />
                <span className={styles.mono}>
                  {check.detail || JSON.stringify(check.resolvers ?? {})}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {selected ? (
        <div className={styles.split}>
          <Panel title="DNS records" subtitle="Create both, then verify. Checks run against several resolvers.">
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Name</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>TXT</td>
                  <td className={styles.mono}>{selected.ownership_record_name}</td>
                  <td className={styles.mono}>the ownership token shown at claim time</td>
                </tr>
                <tr>
                  <td>CNAME</td>
                  <td className={styles.mono}>{selected.domain}</td>
                  <td className={styles.mono}>{selected.cname_target}</td>
                </tr>
              </tbody>
            </table>
            {checks.loading ? <Loading what="DNS checks" /> : null}
            {checks.error ? <Failure what="DNS checks" error={checks.error} onRetry={checks.reload} /> : null}
            {checks.data && checks.data.checks.length === 0 ? (
              <Empty>No verification attempt has been recorded yet.</Empty>
            ) : null}
            {(checks.data?.checks ?? []).length > 0 ? (
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Check</th>
                    <th>Result</th>
                    <th>Consistent</th>
                    <th>Detail</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {(checks.data?.checks ?? []).map((check) => (
                    <tr key={check.id}>
                      <td>{check.check_type}</td>
                      <td>
                        <StatusPill tone={check.passed ? "ok" : "bad"} label={check.passed ? "Passed" : "Failed"} />
                      </td>
                      <td>{check.consistent ? "Yes" : "No"}</td>
                      <td className={styles.mono}>{check.detail}</td>
                      <td>{timestampLabel(check.checked_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </Panel>

          <Panel title="Certificate" subtitle="Issued by the edge on demand; renewal is watched daily.">
            {certificate.loading ? <Loading what="certificate" /> : null}
            {certificate.error ? (
              <Failure what="certificate" error={certificate.error} onRetry={certificate.reload} />
            ) : null}
            {certificate.data ? (
              <ul className={styles.checklist}>
                <li>
                  <StatusPill
                    tone={certificateTone(certificate.data.state, certificate.data.renewal_due)}
                    label={certificate.data.state}
                  />
                  <span>
                    {certificate.data.issuer || "issuer unknown"} · expires{" "}
                    {certificate.data.not_after ? timestampLabel(certificate.data.not_after) : "unknown"}
                  </span>
                </li>
                <li>
                  <span>
                    {certificate.data.days_to_expiry === null
                      ? "Days to expiry unknown"
                      : `${certificate.data.days_to_expiry} days to expiry`}
                  </span>
                </li>
                {certificate.data.error ? (
                  <li>
                    <StatusPill tone="bad" label="Error" />
                    <span className={styles.mono}>{certificate.data.error}</span>
                  </li>
                ) : null}
              </ul>
            ) : null}
            <Callout tone="info">
              A pending certificate is not a live hostname: the domain only becomes routable
              when the status above reads Live.
            </Callout>
          </Panel>
        </div>
      ) : null}
    </>
  );
}
