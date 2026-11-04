/**
 * dashboard/src/pages/campaigns/CampaignsPage.tsx
 * Campaign list with create/edit and live progress.
 *
 * Data: `api/campaigns.ts` → `/api/campaigns` (list, create, patch) and
 * `/api/campaigns/{id}/kpis` (progress) from app/api/campaign_routes.py.
 *
 * Progress is only rendered from the server's KPI document
 * (`campaign_service.progress()`: total_leads/attempted/conversions/dnc_skipped/
 * failed). Nothing is estimated in the browser, so a campaign with no
 * collection yet shows zeros rather than an implied percentage.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Campaign,
  CampaignInput,
  CampaignKpis,
  createCampaign,
  getCampaignKpis,
  listCampaigns,
  updateCampaign,
} from '../../api/campaigns';
import {
  Badge,
  ConsoleCard,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  buttonStyle,
  inputStyle,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

const EMPTY_FORM: CampaignInput = {
  name: '',
  goal: '',
  channel: 'voice',
  opening_line: '',
  script_prompt: '',
  timezone: 'UTC',
  calls_per_minute: 5,
  daily_limit: 100,
  max_attempts_per_lead: 3,
  daily_start: '09:00',
  daily_end: '17:00',
  days_of_week: [1, 2, 3, 4, 5],
};

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [kpis, setKpis] = useState<Record<string, CampaignKpis>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<CampaignInput>(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listCampaigns();
      setCampaigns(rows);
      // Progress is a per-campaign document; fetch them together and keep the
      // map keyed by id so a refresh never renders another campaign's numbers.
      const entries = await Promise.all(
        rows.map(async (campaign) => {
          try {
            return [campaign.id, await getCampaignKpis(campaign.id, campaign.environment_id)] as const;
          } catch {
            return [campaign.id, null] as const;
          }
        }),
      );
      const next: Record<string, CampaignKpis> = {};
      for (const [id, value] of entries) if (value) next[id] = value;
      setKpis(next);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totals = useMemo(() => {
    const rows = Object.values(kpis);
    return {
      leads: rows.reduce((sum, row) => sum + row.total_leads, 0),
      attempted: rows.reduce((sum, row) => sum + row.attempted, 0),
      conversions: rows.reduce((sum, row) => sum + row.conversions, 0),
    };
  }, [kpis]);

  function startCreate() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setShowForm(true);
  }

  function startEdit(campaign: Campaign) {
    setEditingId(campaign.id);
    setForm({
      name: campaign.name,
      goal: campaign.goal,
      channel: campaign.channel,
      opening_line: campaign.opening_line,
      script_prompt: campaign.script_prompt,
      timezone: campaign.timezone,
      calls_per_minute: campaign.calls_per_minute,
      daily_limit: campaign.daily_limit,
      max_attempts_per_lead: campaign.max_attempts_per_lead,
      daily_start: campaign.daily_start,
      daily_end: campaign.daily_end,
      days_of_week: campaign.days_of_week,
    });
    setFormError(null);
    setShowForm(true);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!form.name.trim()) {
      setFormError(new Error('A campaign name is required.'));
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      if (editingId) await updateCampaign(editingId, form);
      else await createCampaign(form);
      setShowForm(false);
      setForm(EMPTY_FORM);
      setEditingId(null);
      await load();
    } catch (e) {
      setFormError(e);
    } finally {
      setSaving(false);
    }
  }

  function toggleDay(day: number) {
    const current = form.days_of_week ?? [];
    const next = current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort();
    setForm({ ...form, days_of_week: next });
  }

  if (loading) return <LoadingState label="Loading campaigns…" />;

  return (
    <main data-testid="campaigns-page" style={{ padding: 20, color: '#e9eef5' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 20, margin: 0 }}>Campaigns</h1>
          <p style={{ margin: '4px 0 0', fontSize: 12.5, color: '#8fa3b8' }}>
            {campaigns.length} campaign{campaigns.length === 1 ? '' : 's'} · {totals.leads} leads · {totals.attempted} attempted · {totals.conversions} converted
          </p>
        </div>
        <button style={buttonStyle} onClick={startCreate} data-testid="campaign-new">
          New campaign
        </button>
      </div>

      <ErrorState error={error} onRetry={() => void load()} />

      {showForm && (
        <ConsoleCard
          title={editingId ? 'Edit campaign' : 'Create campaign'}
          testId="campaign-form"
          actions={
            <button style={subtleButtonStyle} onClick={() => setShowForm(false)}>
              Close
            </button>
          }
        >
          <ErrorState error={formError} />
          <form onSubmit={submit}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
              <Field label="Name">
                <input
                  style={inputStyle}
                  data-testid="campaign-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </Field>
              <Field label="Goal">
                <input style={inputStyle} value={form.goal ?? ''} onChange={(e) => setForm({ ...form, goal: e.target.value })} />
              </Field>
              <Field label="Channel">
                <select style={inputStyle} value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
                  <option value="voice">voice</option>
                  <option value="sms">sms</option>
                  <option value="whatsapp">whatsapp</option>
                </select>
              </Field>
              <Field label="Timezone">
                <input style={inputStyle} value={form.timezone ?? ''} onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
              </Field>
              <Field label="Calls per minute">
                <input
                  type="number"
                  min={1}
                  style={inputStyle}
                  value={form.calls_per_minute ?? 1}
                  onChange={(e) => setForm({ ...form, calls_per_minute: Number(e.target.value) })}
                />
              </Field>
              <Field label="Daily limit">
                <input
                  type="number"
                  min={0}
                  style={inputStyle}
                  value={form.daily_limit ?? 0}
                  onChange={(e) => setForm({ ...form, daily_limit: Number(e.target.value) })}
                />
              </Field>
              <Field label="Max attempts per lead">
                <input
                  type="number"
                  min={1}
                  style={inputStyle}
                  value={form.max_attempts_per_lead ?? 1}
                  onChange={(e) => setForm({ ...form, max_attempts_per_lead: Number(e.target.value) })}
                />
              </Field>
              <Field label="Calling window start">
                <input style={inputStyle} value={form.daily_start ?? ''} onChange={(e) => setForm({ ...form, daily_start: e.target.value })} placeholder="09:00" />
              </Field>
              <Field label="Calling window end">
                <input style={inputStyle} value={form.daily_end ?? ''} onChange={(e) => setForm({ ...form, daily_end: e.target.value })} placeholder="17:00" />
              </Field>
            </div>

            <Field label="Opening line">
              <input style={inputStyle} value={form.opening_line ?? ''} onChange={(e) => setForm({ ...form, opening_line: e.target.value })} />
            </Field>
            <Field label="Script prompt">
              <textarea
                style={{ ...inputStyle, minHeight: 70 }}
                value={form.script_prompt ?? ''}
                onChange={(e) => setForm({ ...form, script_prompt: e.target.value })}
              />
            </Field>

            <Field label="Days of week (1 = Monday … 7 = Sunday)">
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {DAY_LABELS.map((label, index) => {
                  const day = index + 1;
                  const active = (form.days_of_week ?? []).includes(day);
                  return (
                    <button
                      type="button"
                      key={label}
                      onClick={() => toggleDay(day)}
                      aria-pressed={active}
                      style={{
                        ...subtleButtonStyle,
                        background: active ? '#1b3a6b' : 'transparent',
                        color: active ? '#fff' : '#a9bccf',
                      }}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </Field>

            <button type="submit" style={buttonStyle} disabled={saving} data-testid="campaign-save">
              {saving ? 'Saving…' : editingId ? 'Save changes' : 'Create campaign'}
            </button>
          </form>
        </ConsoleCard>
      )}

      <ConsoleCard title="All campaigns" testId="campaign-list">
        {campaigns.length === 0 ? (
          <EmptyState
            title="No campaigns yet"
            detail="Create one to define an audience, a calling window and a script. Nothing is dialled until the campaign is scheduled and started."
          />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>State</th>
                <th style={thStyle}>Channel</th>
                <th style={thStyle}>Leads</th>
                <th style={thStyle}>Attempted</th>
                <th style={thStyle}>Converted</th>
                <th style={thStyle}>DNC skipped</th>
                <th style={thStyle}>Failed</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {campaigns.map((campaign) => {
                const progress = kpis[campaign.id];
                return (
                  <tr key={campaign.id} data-testid={`campaign-row-${campaign.id}`}>
                    <td style={tdStyle}>
                      <a href={`/dashboard/campaigns/${campaign.id}`} style={{ color: '#6ea8ff' }}>
                        {campaign.name}
                      </a>
                      <div style={{ fontSize: 10.5, color: '#5d7186' }}>{campaign.id}</div>
                    </td>
                    <td style={tdStyle}>
                      <Badge tone={campaign.state === 'running' ? 'ok' : campaign.state === 'paused' ? 'warn' : 'neutral'}>
                        {campaign.state}
                      </Badge>
                    </td>
                    <td style={tdStyle}>{campaign.channel}</td>
                    <td style={tdStyle}>{progress ? progress.total_leads : '—'}</td>
                    <td style={tdStyle}>{progress ? progress.attempted : '—'}</td>
                    <td style={tdStyle}>{progress ? progress.conversions : '—'}</td>
                    <td style={tdStyle}>{progress ? progress.dnc_skipped : '—'}</td>
                    <td style={tdStyle}>{progress ? progress.failed : '—'}</td>
                    <td style={tdStyle}>
                      <button style={subtleButtonStyle} onClick={() => startEdit(campaign)}>
                        Edit
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </main>
  );
}

export default CampaignsPage;
