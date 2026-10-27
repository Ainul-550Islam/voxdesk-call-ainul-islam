"use client";

import { useEffect, useState } from "react";

import { Callout, Empty, Failure, Loading, Panel, StatusPill } from "../components/Primitives";
import { plat2Api } from "../lib/api";
import type { BrandAssetRow, ThemeContrastEntry, ThemeResponse, ThemeVersionRow } from "../lib/api";
import { contrastFailures, formatNumber, timestampLabel } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "../platform-admin.module.css";

/**
 * Branding: theme colours and brand assets.
 *
 * The editor validates through `/theme/validate` before it will let anything be
 * published, so a contrast failure is a blocked save rather than a warning beside a
 * working button. `4.5:1` for body text and `3:1` for UI lines are WCAG AA, not taste.
 */
export default function BrandingPage() {
  const theme = useApi<ThemeResponse>(() => plat2Api.readTheme(), []);
  const assets = useApi<{ assets: BrandAssetRow[] }>(() => plat2Api.listAssets(), []);

  const [draft, setDraft] = useState<string>("{}");
  const [pairs, setPairs] = useState<ThemeContrastEntry[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [failures, setFailures] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState<ThemeVersionRow | null>(null);

  // Seed the editor from the active payload once it loads; the textarea stays the
  // single source of truth afterwards so a keystroke is never overwritten by a refetch.
  useEffect(() => {
    if (theme.data) setDraft(JSON.stringify(theme.data.payload, null, 2));
  }, [theme.data]);

  async function validate() {
    setBusy(true);
    setValidationError(null);
    setPublished(null);
    setFailures([]);
    try {
      const payload = JSON.parse(draft) as Record<string, unknown>;
      const result = await plat2Api.validateTheme(payload);
      setPairs(result.contrast.pairs ?? []);
      setFailures(contrastFailures(result.contrast));
    } catch (cause) {
      setPairs([]);
      setValidationError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    setBusy(true);
    setValidationError(null);
    try {
      const payload = JSON.parse(draft) as Record<string, unknown>;
      const version = await plat2Api.publishTheme(payload, { activate: true });
      setPublished(version);
      theme.reload();
    } catch (cause) {
      setValidationError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const canPublish = failures.length === 0 && validationError === null;

  return (
    <>
      <h1 className={styles.pageTitle}>Branding</h1>
      <p className={styles.pageLead}>
        Theme colours are validated for contrast before they are stored, and the same
        payload feeds the widget, the branded email templates and the mobile app. A theme
        that fails contrast cannot be published — the server refuses it, not just this page.
      </p>

      <Panel
        title="Active theme"
        subtitle="Version history is kept; publishing activates a new version."
        actions={
          theme.data?.version ? (
            <StatusPill tone="ok" label={`Version ${theme.data.version.version_number}`} />
          ) : (
            <StatusPill tone="neutral" label="Defaults" />
          )
        }
      >
        {theme.loading ? <Loading what="theme" /> : null}
        {theme.error ? <Failure what="theme" error={theme.error} onRetry={theme.reload} /> : null}
        {theme.data ? (
          <>
            <p className={styles.metricHint}>
              Allowed fonts: {(theme.data.allowed_fonts ?? []).join(", ") || "—"}
            </p>
            <textarea
              className={styles.mono}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={16}
              spellCheck={false}
              style={{ width: "100%", padding: 10, borderRadius: 8, border: "1px solid #c7ccd4" }}
              aria-label="Theme payload"
            />
            <div className={styles.inlineForm} style={{ marginTop: 10 }}>
              <button type="button" className={styles.button} onClick={validate} disabled={busy}>
                {busy ? "Checking…" : "Validate contrast"}
              </button>
              <button
                type="button"
                className={`${styles.button} ${styles.buttonPrimary}`}
                onClick={publish}
                disabled={busy || !canPublish}
              >
                Publish theme
              </button>
            </div>
            {validationError ? <Callout tone="bad">{validationError}</Callout> : null}
            {failures.length > 0 ? (
              <Callout tone="bad">
                Contrast failures block publishing: {failures.join("; ")}
              </Callout>
            ) : null}
            {published ? (
              <Callout tone="ok">
                Published version {published.version_number} at {timestampLabel(published.created_at)}.
              </Callout>
            ) : null}
          </>
        ) : null}
      </Panel>

      {pairs.length > 0 ? (
        <Panel title="Contrast report" subtitle="Measured on the colours in the editor, as opaque.">
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Foreground</th>
                <th>Background</th>
                <th>Ratio</th>
                <th>Minimum</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((pair) => (
                <tr key={`${pair.foreground}-${pair.background}`}>
                  <td className={styles.mono}>{pair.foreground}</td>
                  <td className={styles.mono}>{pair.background}</td>
                  <td className={styles.numeric}>{formatNumber(pair.ratio, 2)}:1</td>
                  <td className={styles.numeric}>{formatNumber(pair.minimum, 1)}:1</td>
                  <td>
                    <StatusPill tone={pair.passed ? "ok" : "bad"} label={pair.passed ? "Pass" : "Fail"} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}

      <Panel
        title="Brand assets"
        subtitle="SVG is sanitised on upload; the stored digest is what the CDN caches against."
      >
        {assets.loading ? <Loading what="assets" /> : null}
        {assets.error ? <Failure what="assets" error={assets.error} onRetry={assets.reload} /> : null}
        {assets.data && assets.data.assets.length === 0 ? (
          <Empty>No brand asset uploaded yet.</Empty>
        ) : null}
        {(assets.data?.assets ?? []).length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Kind</th>
                <th>File</th>
                <th>Type</th>
                <th>Size</th>
                <th>Sanitised</th>
                <th>SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {(assets.data?.assets ?? []).map((asset) => (
                <tr key={asset.id}>
                  <td>{asset.kind}</td>
                  <td className={styles.mono}>{asset.filename}</td>
                  <td className={styles.mono}>{asset.content_type}</td>
                  <td className={styles.numeric}>{asset.size_bytes.toLocaleString("en-GB")} B</td>
                  <td>
                    <StatusPill
                      tone={asset.sanitized ? "ok" : "neutral"}
                      label={asset.sanitized ? "Cleaned" : "As uploaded"}
                    />
                  </td>
                  <td className={styles.mono}>{asset.sha256.slice(0, 16)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>
    </>
  );
}
