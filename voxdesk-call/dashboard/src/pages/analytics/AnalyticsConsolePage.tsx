/**
 * dashboard/src/pages/analytics/AnalyticsConsolePage.tsx
 * Calls, conversion funnel and metered usage for a chosen window.
 *
 * Data: `api/analytics.ts` →
 *   GET /api/analytics/overview | /calls | /conversion | /usage
 *   GET /api/roi/kpis  (and /api/roi/measurement/{period})
 *
 * The window is resolved by the server in the tenant's timezone
 * (`resolve_window`): this page sends either a named preset or inclusive local
 * dates and renders the `window` block that comes back, never its own idea of
 * the date range. `usage` is optional in the overview because it is omitted for
 * callers without the billing read permission.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ANALYTICS_PRESETS,
  AnalyticsWindow,
  CallAnalytics,
  ConversionAnalytics,
  HourBucket,
  OverviewAnalytics,
  UsageAnalytics,
  WindowQuery,
  formatPercent,
  getAnalyticsOverview,
  getCallAnalytics,
  getConversionAnalytics,
  getUsageAnalytics,
} from '../../api/analytics';
import {
  ConsoleCard,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  SpanBars,
  buttonStyle,
  inputStyle,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

type Preset = (typeof ANALYTICS_PRESETS)[number]['value'];
type PresetChoice = Preset | 'custom';

export function AnalyticsConsolePage() {
  const [preset, setPreset] = useState<PresetChoice>('last_7_days');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [query, setQuery] = useState<WindowQuery>({ range: 'last_7_days' });
  const [windowInfo, setWindowInfo] = useState<AnalyticsWindow | null>(null);
  const [overview, setOverview] = useState<OverviewAnalytics | null>(null);
  const [calls, setCalls] = useState<CallAnalytics | null>(null);
  const [conversion, setConversion] = useState<ConversionAnalytics | null>(null);
  const [usage, setUsage] = useState<UsageAnalytics | null>(null);
  const [usageUnavailable, setUsageUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async (next: WindowQuery) => {
    setLoading(true);
    setError(null);
    try {
      const [overviewResult, callsResult, conversionResult] = await Promise.all([
        getAnalyticsOverview(next),
        getCallAnalytics(next),
        getConversionAnalytics(next),
      ]);
      setOverview(overviewResult);
      setCalls(callsResult);
      setConversion(conversionResult);
      setWindowInfo(overviewResult.window);
      // Usage is metered per billing period, not per query window; it is guarded
      // by the billing read permission, so a 403 is an expected state, not a page
      // failure.
      try {
        setUsage(await getUsageAnalytics());
        setUsageUnavailable(false);
      } catch {
        setUsage(null);
        setUsageUnavailable(true);
      }
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(query);
  }, [load, query]);

  function apply(event: React.FormEvent) {
    event.preventDefault();
    if (preset === 'custom') {
      if (!customStart || !customEnd) {
        setFormError('Both dates are required for a custom window.');
        return;
      }
      if (customStart > customEnd) {
        setFormError('The start date must not be after the end date.');
        return;
      }
      setFormError(null);
      setQuery({ start: customStart, end: customEnd });
      return;
    }
    setFormError(null);
    setQuery({ range: preset });
  }

  const hourly = useMemo<HourBucket[]>(() => {
    if (!calls) return [];
    return [...calls.by_hour].sort((a, b) => a.hour - b.hour);
  }, [calls]);

  const statusRows = useMemo(() => (calls ? Object.entries(calls.by_status) : []), [calls]);
  const directionRows = useMemo(() => (calls ? Object.entries(calls.by_direction) : []), [calls]);
  const maxHourly = hourly.reduce((max, bucket) => Math.max(max, bucket.total), 0);

  return (
    <main data-testid="analytics-console-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Analytics</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        {windowInfo
          ? `${windowInfo.label}: ${new Date(windowInfo.start).toLocaleString()} → ${new Date(windowInfo.end).toLocaleString()} (${windowInfo.timezone}, ${windowInfo.days} day${windowInfo.days === 1 ? '' : 's'})`
          : 'Windows are resolved server-side in the workspace timezone.'}
      </p>

      <ErrorState error={error} onRetry={() => void load(query)} />

      <ConsoleCard title="Window" testId="analytics-window">
        <form onSubmit={apply}>
          {formError && (
            <div role="alert" style={{ background: '#2a1010', border: '1px solid #7a2222', borderRadius: 8, padding: 10, marginBottom: 10, fontSize: 12.5 }}>
              {formError}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            {ANALYTICS_PRESETS.map((option) => (
              <button
                type="button"
                key={option.value}
                data-testid={`preset-${option.value}`}
                onClick={() => setPreset(option.value)}
                style={{
                  ...subtleButtonStyle,
                  background: preset === option.value ? '#1b3a6b' : 'transparent',
                  color: preset === option.value ? '#fff' : '#a9bccf',
                }}
              >
                {option.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setPreset('custom')}
              style={{ ...subtleButtonStyle, background: preset === 'custom' ? '#1b3a6b' : 'transparent', color: preset === 'custom' ? '#fff' : '#a9bccf' }}
            >
              Custom
            </button>
          </div>
          {preset === 'custom' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12, marginTop: 12 }}>
              <Field label="Start date">
                <input style={inputStyle} type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
              </Field>
              <Field label="End date (inclusive)">
                <input style={inputStyle} type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
              </Field>
            </div>
          )}
          <button type="submit" style={buttonStyle} disabled={loading} data-testid="analytics-apply">
            {loading ? 'Loading…' : 'Apply window'}
          </button>
        </form>
      </ConsoleCard>

      {loading && !calls ? (
        <LoadingState label="Loading analytics…" />
      ) : (
        <>
          <ConsoleCard title="Call totals" testId="analytics-totals">
            {!calls || calls.totals.total === 0 ? (
              <EmptyState title="No calls in this window" detail="Try a wider window. A zero total is the honest answer here." />
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 12, marginBottom: 14 }}>
                  {(
                    [
                      ['Total', calls.totals.total],
                      ['Answered', calls.totals.answered],
                      ['Missed', calls.totals.missed],
                      ['Failed', calls.totals.failed],
                      ['Booked', calls.totals.booked],
                      ['Transferred', calls.totals.transferred],
                      ['Eligible', calls.totals.eligible],
                      ['Talk time', `${Math.round(calls.totals.total_seconds / 60)} min`],
                    ] as const
                  ).map(([label, value]) => (
                    <div key={label}>
                      <div style={{ fontSize: 11, color: '#8fa3b8' }}>{label}</div>
                      <div style={{ fontSize: 18 }}>{value}</div>
                    </div>
                  ))}
                </div>
                <p style={{ fontSize: 11.5, color: '#8fa3b8', margin: 0 }}>
                  Average answered call: {calls.totals.average_seconds ?? 0}s ·{' '}
                  {calls.totals.minutes ?? Math.round(calls.totals.total_seconds / 60)} total minutes.
                  Rates are shown with the funnel, where their denominators are stated.
                </p>
              </>
            )}
          </ConsoleCard>

          <ConsoleCard title="By status and direction" testId="analytics-breakdown">
            {statusRows.length === 0 && directionRows.length === 0 ? (
              <EmptyState title="No breakdown" />
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 16 }}>
                <div>
                  <h3 style={{ fontSize: 12.5, color: '#8fa3b8', margin: '0 0 8px' }}>Status</h3>
                  <SpanBars data={statusRows} max={Math.max(...statusRows.map(([, v]) => v), 0)} />
                </div>
                <div>
                  <h3 style={{ fontSize: 12.5, color: '#8fa3b8', margin: '0 0 8px' }}>Direction</h3>
                  <SpanBars data={directionRows} max={Math.max(...directionRows.map(([, v]) => v), 0)} />
                </div>
              </div>
            )}
          </ConsoleCard>

          <ConsoleCard title="Calls by hour" testId="analytics-by-hour">
            {hourly.length === 0 ? (
              <EmptyState title="No hourly data" />
            ) : (
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 120 }}>
                {hourly.map((bucket) => (
                  <div key={bucket.hour} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%' }} title={`${bucket.hour}:00 — ${bucket.total} total, ${bucket.missed} missed`}>
                    <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', width: '100%' }}>
                      <div
                        style={{
                          width: '100%',
                          height: `${maxHourly === 0 ? 0 : (bucket.total / maxHourly) * 100}%`,
                          background: '#2f6fed',
                          borderRadius: 3,
                        }}
                      />
                    </div>
                    <span style={{ fontSize: 9, color: '#5d7186' }}>{bucket.hour}</span>
                  </div>
                ))}
              </div>
            )}
          </ConsoleCard>

          <ConsoleCard title="Daily series" testId="analytics-series">
            {!calls || calls.series.length === 0 ? (
              <EmptyState title="No daily rows" />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Date</th>
                    <th style={thStyle}>Answered</th>
                    <th style={thStyle}>Missed</th>
                    <th style={thStyle}>Failed</th>
                    <th style={thStyle}>Booked</th>
                    <th style={thStyle}>Transferred</th>
                    <th style={thStyle}>Minutes</th>
                  </tr>
                </thead>
                <tbody>
                  {calls.series.map((point) => (
                    <tr key={point.date}>
                      <td style={tdStyle}>{point.date}</td>
                      <td style={tdStyle}>{point.answered}</td>
                      <td style={tdStyle}>{point.missed}</td>
                      <td style={tdStyle}>{point.failed}</td>
                      <td style={tdStyle}>{point.booked}</td>
                      <td style={tdStyle}>{point.transferred}</td>
                      <td style={tdStyle}>{point.minutes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>

          <ConsoleCard title="Conversion funnel" testId="analytics-funnel">
            {!conversion ? (
              <EmptyState title="No funnel" />
            ) : (
              <>
                <SpanBars
                  data={conversion.funnel.map((stage) => [stage.stage, stage.count] as [string, number])}
                  max={Math.max(...conversion.funnel.map((stage) => stage.count), 0)}
                />
                <table style={{ ...tableStyle, marginTop: 12 }}>
                  <tbody>
                    {Object.entries(conversion.rates).map(([rate, value]) => (
                      <tr key={rate}>
                        <td style={{ ...tdStyle, width: 220 }}>{rate.replace(/_/g, ' ')}</td>
                        <td style={tdStyle}>{formatPercent(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </ConsoleCard>

          <ConsoleCard title="Metered usage" testId="analytics-usage">
            {usageUnavailable ? (
              <EmptyState
                title="Usage is not visible to this account"
                detail="The /api/analytics/usage endpoint requires the billing read permission; the rest of this page is unaffected."
              />
            ) : !usage ? (
              <EmptyState title="No usage document" />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Metric</th>
                    <th style={thStyle}>Used</th>
                    <th style={thStyle}>Included</th>
                    <th style={thStyle}>Overage</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.metrics.map((metric) => (
                    <tr key={metric.metric}>
                      <td style={tdStyle}>{metric.metric}</td>
                      <td style={tdStyle}>
                        {metric.used} {metric.unit}
                      </td>
                      <td style={tdStyle}>{metric.included}</td>
                      <td style={tdStyle}>{metric.overage}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>

          {overview?.operations && (
            <ConsoleCard title="Operations snapshot" testId="analytics-operations">
              <pre style={{ margin: 0, fontSize: 11.5, whiteSpace: 'pre-wrap' }}>{JSON.stringify(overview.operations, null, 2)}</pre>
            </ConsoleCard>
          )}
        </>
      )}
    </main>
  );
}

export default AnalyticsConsolePage;
