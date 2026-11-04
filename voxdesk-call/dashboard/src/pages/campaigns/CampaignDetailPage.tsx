/**
 * dashboard/src/pages/campaigns/CampaignDetailPage.tsx
 * One campaign: audience, planned execution intents, call results, pause/resume.
 *
 * Data: `api/campaigns.ts` →
 *   GET  /api/campaigns/{id}                    → definition
 *   GET  /api/campaigns/{id}/audience           → {segment_ids, lead_count}
 *   GET  /api/campaigns/{id}/eligibility        → {eligible, skipped, intents}
 *   POST /api/campaigns/{id}/plan?limit=        → ordered ExecutionIntent list
 *   GET  /api/campaigns/{id}/results            → {metrics, eligibility}
 *   GET  /api/campaigns/{id}/kpis               → progress document
 *   POST /api/campaigns/{id}/pause | /resume | /schedule | /cancel
 *
 * The audience count and the planned intents are different answers to different
 * questions ("how many leads match" vs "what would be dialled next"), so they are
 * rendered side by side rather than merged into one number.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Campaign,
  CampaignAudience,
  CampaignEligibility,
  CampaignKpis,
  CampaignResults,
  ExecutionIntent,
  cancelCampaign,
  getCampaign,
  getCampaignAudience,
  getCampaignEligibility,
  getCampaignKpis,
  getCampaignResults,
  pauseCampaign,
  planCampaign,
  resumeCampaign,
  scheduleCampaign,
} from '../../api/campaigns';
import {
  Badge,
  ConsoleCard,
  EmptyState,
  ErrorState,
  LoadingState,
  buttonStyle,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

export interface CampaignDetailPageProps {
  campaignId: string;
}

export function CampaignDetailPage({ campaignId }: CampaignDetailPageProps) {
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [audience, setAudience] = useState<CampaignAudience | null>(null);
  const [eligibility, setEligibility] = useState<CampaignEligibility | null>(null);
  const [plan, setPlan] = useState<ExecutionIntent[]>([]);
  const [results, setResults] = useState<CampaignResults | null>(null);
  const [kpis, setKpis] = useState<CampaignKpis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [planLimit, setPlanLimit] = useState(25);

  const load = useCallback(
    async (environmentId?: string) => {
      setLoading(true);
      setError(null);
      try {
        const definition = await getCampaign(campaignId, environmentId);
        const env = definition.environment_id;
        const [audienceResult, eligibilityResult, planResult, resultsResult, kpisResult] = await Promise.all([
          getCampaignAudience(campaignId, env),
          getCampaignEligibility(campaignId, env),
          planCampaign(campaignId, planLimit, env).catch(() => [] as ExecutionIntent[]),
          getCampaignResults(campaignId, env),
          getCampaignKpis(campaignId, env),
        ]);
        setCampaign(definition);
        setAudience(audienceResult);
        setEligibility(eligibilityResult);
        setPlan(planResult);
        setResults(resultsResult);
        setKpis(kpisResult);
      } catch (e) {
        setError(e);
      } finally {
        setLoading(false);
      }
    },
    [campaignId, planLimit],
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function transition(key: string, action: () => Promise<Campaign>) {
    setBusy(key);
    setActionError(null);
    try {
      const next = await action();
      setCampaign(next);
      const refreshed = await getCampaignKpis(campaignId, next.environment_id);
      setKpis(refreshed);
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <LoadingState label="Loading campaign…" />;

  const env = campaign?.environment_id;

  return (
    <main data-testid="campaign-detail-page" style={{ padding: 20, color: '#e9eef5' }}>
      <p style={{ margin: '0 0 8px' }}>
        <a href="/dashboard/campaigns" style={{ color: '#6ea8ff', fontSize: 12.5 }}>
          ← All campaigns
        </a>
      </p>

      <ErrorState error={error} onRetry={() => void load()} />
      <ErrorState error={actionError} />

      {!campaign ? (
        <EmptyState title="Campaign not found" detail="The campaign may have been deleted or belongs to another environment." />
      ) : (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
            <div>
              <h1 style={{ fontSize: 20, margin: 0 }}>{campaign.name}</h1>
              <p style={{ margin: '4px 0 0', fontSize: 12.5, color: '#8fa3b8' }}>
                <Badge tone={campaign.state === 'running' ? 'ok' : campaign.state === 'paused' ? 'warn' : 'neutral'}>
                  {campaign.state}
                </Badge>{' '}
                · {campaign.channel} · {campaign.timezone} · {campaign.daily_start}–{campaign.daily_end} ·{' '}
                {campaign.calls_per_minute}/min · limit {campaign.daily_limit}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                style={subtleButtonStyle}
                data-testid="campaign-schedule"
                disabled={busy !== null}
                onClick={() => transition('schedule', () => scheduleCampaign(campaignId, env))}
              >
                {busy === 'schedule' ? 'Scheduling…' : 'Schedule'}
              </button>
              <button
                style={subtleButtonStyle}
                data-testid="campaign-pause"
                disabled={busy !== null || campaign.state === 'paused'}
                onClick={() => transition('pause', () => pauseCampaign(campaignId, env))}
              >
                {busy === 'pause' ? 'Pausing…' : 'Pause'}
              </button>
              <button
                style={buttonStyle}
                data-testid="campaign-resume"
                disabled={busy !== null || campaign.state === 'running'}
                onClick={() => transition('resume', () => resumeCampaign(campaignId, env))}
              >
                {busy === 'resume' ? 'Resuming…' : 'Resume'}
              </button>
              <button
                style={subtleButtonStyle}
                data-testid="campaign-cancel"
                disabled={busy !== null || campaign.state === 'cancelled'}
                onClick={() => transition('cancel', () => cancelCampaign(campaignId, env))}
              >
                {busy === 'cancel' ? 'Cancelling…' : 'Cancel'}
              </button>
            </div>
          </div>

          <ConsoleCard title="Progress" testId="campaign-progress">
            {!kpis ? (
              <EmptyState title="No progress yet" detail="Progress appears after the first collection run." />
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: 12 }}>
                {(
                  [
                    ['Leads', kpis.total_leads],
                    ['Attempted', kpis.attempted],
                    ['Converted', kpis.conversions],
                    ['DNC skipped', kpis.dnc_skipped],
                    ['Failed', kpis.failed],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <div style={{ fontSize: 11, color: '#8fa3b8' }}>{label}</div>
                    <div style={{ fontSize: 20 }} data-testid={`kpi-${label.toLowerCase().replace(/\s/g, '-')}`}>
                      {value}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </ConsoleCard>

          <ConsoleCard
            title="Audience"
            testId="campaign-audience"
            description={
              audience
                ? `${audience.lead_count} leads resolve from ${audience.segment_ids.length} segment(s)`
                : 'Audience is resolved server-side, tenant and environment scoped.'
            }
          >
            {eligibility ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
                <div>
                  <div style={{ fontSize: 11, color: '#8fa3b8' }}>Eligible to dial</div>
                  <div style={{ fontSize: 20 }} data-testid="audience-eligible">
                    {eligibility.eligible}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: '#8fa3b8' }}>Skipped (by reason)</div>
                  {Object.keys(eligibility.skipped).length === 0 ? (
                    <div style={{ fontSize: 12.5, color: '#8fa3b8' }}>None skipped</div>
                  ) : (
                    <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: 12.5 }}>
                      {Object.entries(eligibility.skipped).map(([reason, count]) => (
                        <li key={reason}>
                          {reason}: {count}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : (
              <EmptyState title="No eligibility data" detail="The eligibility preview returned nothing for this campaign." />
            )}
          </ConsoleCard>

          <ConsoleCard
            title={`Planned dials (next ${planLimit})`}
            testId="campaign-plan"
            actions={
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={planLimit}
                  onChange={(e) => setPlanLimit(Math.max(1, Math.min(200, Number(e.target.value) || 25)))}
                  style={{ width: 64, padding: '4px 6px', borderRadius: 6, border: '1px solid #24313f', background: '#0a0f15', color: '#e9eef5' }}
                />
                <button style={subtleButtonStyle} onClick={() => void load(env)}>
                  Recompute
                </button>
              </div>
            }
          >
            {plan.length === 0 ? (
              <EmptyState
                title="Nothing planned"
                detail="The planner returned no intents: the audience is empty, every lead is suppressed, or the calling window excludes now."
              />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>#</th>
                    <th style={thStyle}>Lead</th>
                    <th style={thStyle}>Phone</th>
                    <th style={thStyle}>Scheduled</th>
                    <th style={thStyle}>Attempt</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.map((intent, index) => (
                    <tr key={`${intent.lead_id ?? index}-${index}`}>
                      <td style={tdStyle}>{index + 1}</td>
                      <td style={tdStyle}>{String(intent.lead_id ?? '—')}</td>
                      <td style={tdStyle}>{String(intent.phone ?? '—')}</td>
                      <td style={tdStyle}>{String(intent.scheduled_at ?? '—')}</td>
                      <td style={tdStyle}>{String(intent.attempt ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>

          <ConsoleCard title="Call results" testId="campaign-results">
            {!results ? (
              <EmptyState title="No results yet" detail="Results aggregate once calls have been attempted." />
            ) : Object.keys(results.metrics).length === 0 ? (
              <EmptyState title="No call outcomes recorded" detail="The aggregation returned an empty metric set." />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Metric</th>
                    <th style={thStyle}>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(results.metrics).map(([metric, value]) => (
                    <tr key={metric}>
                      <td style={tdStyle}>{metric}</td>
                      <td style={tdStyle}>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>

          <ConsoleCard title="Definition" testId="campaign-definition">
            <pre style={{ margin: 0, fontSize: 11.5, color: '#c8d6e5', whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(
                {
                  id: campaign.id,
                  environment_id: campaign.environment_id,
                  goal: campaign.goal,
                  opening_line: campaign.opening_line,
                  script_prompt: campaign.script_prompt,
                  lead_ids: campaign.lead_ids.length,
                  segment_ids: campaign.segment_ids,
                  days_of_week: campaign.days_of_week,
                  max_attempts_per_lead: campaign.max_attempts_per_lead,
                },
                null,
                2,
              )}
            </pre>
          </ConsoleCard>
        </>
      )}
    </main>
  );
}

export default CampaignDetailPage;
