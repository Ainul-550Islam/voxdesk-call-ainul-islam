/**
 * dashboard/src/tests/campaigns-console.test.tsx
 *
 * The campaign console reads progress from `/api/campaigns/{id}/kpis` and planned
 * dials from `/api/campaigns/{id}/plan`. These tests pin the behaviours that
 * matter operationally: a campaign with no collection shows dashes rather than
 * an invented zero-progress bar, a failed KPI read does not hide the list, and
 * the detail page refuses to dial anything it did not plan server-side.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ApiError } from '../api/client';

const listCampaigns = vi.fn();
const getCampaignKpis = vi.fn();
const getCampaign = vi.fn();
const getCampaignAudience = vi.fn();
const getCampaignEligibility = vi.fn();
const getCampaignResults = vi.fn();
const planCampaign = vi.fn();
const createCampaign = vi.fn();
const updateCampaign = vi.fn();
const scheduleCampaign = vi.fn();
const pauseCampaign = vi.fn();
const resumeCampaign = vi.fn();
const cancelCampaign = vi.fn();

vi.mock('../api/campaigns', () => ({
  listCampaigns: (...args: unknown[]) => listCampaigns(...args),
  getCampaignKpis: (...args: unknown[]) => getCampaignKpis(...args),
  getCampaign: (...args: unknown[]) => getCampaign(...args),
  getCampaignAudience: (...args: unknown[]) => getCampaignAudience(...args),
  getCampaignEligibility: (...args: unknown[]) => getCampaignEligibility(...args),
  getCampaignResults: (...args: unknown[]) => getCampaignResults(...args),
  planCampaign: (...args: unknown[]) => planCampaign(...args),
  createCampaign: (...args: unknown[]) => createCampaign(...args),
  updateCampaign: (...args: unknown[]) => updateCampaign(...args),
  scheduleCampaign: (...args: unknown[]) => scheduleCampaign(...args),
  pauseCampaign: (...args: unknown[]) => pauseCampaign(...args),
  resumeCampaign: (...args: unknown[]) => resumeCampaign(...args),
  cancelCampaign: (...args: unknown[]) => cancelCampaign(...args),
}));

import { CampaignDetailPage } from '../pages/campaigns/CampaignDetailPage';
import { CampaignsPage } from '../pages/campaigns/CampaignsPage';

const CAMPAIGN = {
  id: 'campaign-001',
  tenant_id: 'tenant-001',
  environment_id: 'env-001',
  name: 'Renewals Q4',
  goal: 'book renewal calls',
  channel: 'voice',
  state: 'running',
  timezone: 'Asia/Dhaka',
  calls_per_minute: 5,
  daily_limit: 100,
  max_attempts_per_lead: 3,
  daily_start: '09:00',
  daily_end: '17:00',
  days_of_week: [1, 2, 3, 4, 5],
  opening_line: 'Hi, this is VoxDesk.',
  script_prompt: 'Confirm the renewal date.',
  lead_ids: ['lead-1', 'lead-2'],
  segment_ids: ['seg-1'],
  created_at: '2026-10-01T09:00:00Z',
  updated_at: '2026-10-02T09:00:00Z',
};

beforeEach(() => {
  for (const fn of [
    listCampaigns,
    getCampaignKpis,
    getCampaign,
    getCampaignAudience,
    getCampaignEligibility,
    getCampaignResults,
    planCampaign,
    createCampaign,
    updateCampaign,
    scheduleCampaign,
    pauseCampaign,
    resumeCampaign,
    cancelCampaign,
  ]) {
    fn.mockReset();
  }
});

describe('CampaignsPage', () => {
  it('renders server KPI documents and sums only campaigns that returned one', async () => {
    listCampaigns.mockResolvedValue([CAMPAIGN, { ...CAMPAIGN, id: 'campaign-002', name: 'Win-back' }]);
    getCampaignKpis.mockImplementation(async (id: string) =>
      id === 'campaign-001'
        ? { total_leads: 10, attempted: 4, conversions: 2, dnc_skipped: 1, failed: 0 }
        : Promise.reject(new ApiError('SERVER_ERROR', 500, 'kpi aggregation failed')),
    );

    render(<CampaignsPage />);

    await waitFor(() => expect(screen.getByTestId('campaign-row-campaign-001')).toHaveTextContent('Renewals Q4'));
    expect(screen.getByText(/2 campaigns · 10 leads · 4 attempted · 2 converted/)).toBeInTheDocument();
    // The campaign whose KPI read failed shows em dashes, not zeros.
    const second = screen.getByTestId('campaign-row-campaign-002');
    expect(second).toHaveTextContent('—');
  });

  it('shows the create affordance as an empty state when the workspace has no campaigns', async () => {
    listCampaigns.mockResolvedValue([]);

    render(<CampaignsPage />);

    await waitFor(() => expect(screen.getByText('No campaigns yet')).toBeInTheDocument());
    expect(screen.getByTestId('campaign-new')).toBeInTheDocument();
  });

  it('surfaces a 403 on the list endpoint', async () => {
    listCampaigns.mockRejectedValue(new ApiError('FORBIDDEN', 403, 'Forbidden'));

    render(<CampaignsPage />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/permission/i));
  });
});

describe('CampaignDetailPage', () => {
  it('renders audience, eligibility and the planned dial list from the server', async () => {
    getCampaign.mockResolvedValue(CAMPAIGN);
    getCampaignAudience.mockResolvedValue({ segment_ids: ['seg-1'], lead_count: 10 });
    getCampaignEligibility.mockResolvedValue({ eligible: 7, skipped: { dnc: 2, outside_window: 1 }, intents: [] });
    planCampaign.mockResolvedValue([
      { lead_id: 'lead-1', phone: '+15550001', scheduled_at: '2026-10-05T10:00:00Z', attempt: 1 },
      { lead_id: 'lead-2', phone: '+15550002', scheduled_at: '2026-10-05T10:05:00Z', attempt: 1 },
    ]);
    getCampaignResults.mockResolvedValue({ metrics: { answered: 4, booked: 1 }, eligibility: {} });
    getCampaignKpis.mockResolvedValue({ total_leads: 10, attempted: 4, conversions: 2, dnc_skipped: 1, failed: 0 });

    render(<CampaignDetailPage campaignId="campaign-001" />);

    await waitFor(() => expect(screen.getByTestId('audience-eligible')).toHaveTextContent('7'));
    expect(screen.getByText('dnc: 2')).toBeInTheDocument();
    expect(screen.getByText('+15550001')).toBeInTheDocument();
    expect(screen.getByTestId('kpi-converted')).toHaveTextContent('2');
    expect(screen.getByText('answered')).toBeInTheDocument();
  });

  it('explains an empty plan instead of showing an empty table', async () => {
    getCampaign.mockResolvedValue({ ...CAMPAIGN, state: 'paused' });
    getCampaignAudience.mockResolvedValue({ segment_ids: [], lead_count: 0 });
    getCampaignEligibility.mockResolvedValue({ eligible: 0, skipped: {}, intents: [] });
    planCampaign.mockResolvedValue([]);
    getCampaignResults.mockResolvedValue({ metrics: {}, eligibility: {} });
    getCampaignKpis.mockResolvedValue({ total_leads: 0, attempted: 0, conversions: 0, dnc_skipped: 0, failed: 0 });

    render(<CampaignDetailPage campaignId="campaign-001" />);

    await waitFor(() => expect(screen.getByText('Nothing planned')).toBeInTheDocument());
    expect(screen.getByText('No call outcomes recorded')).toBeInTheDocument();
    // Paused campaigns offer resume, not another pause.
    expect(screen.getByTestId('campaign-resume')).not.toBeDisabled();
    expect(screen.getByTestId('campaign-pause')).toBeDisabled();
  });

  it('reports a missing campaign as a 404 explanation rather than a blank header', async () => {
    getCampaign.mockRejectedValue(new ApiError('NOT_FOUND', 404, 'Campaign not found'));
    getCampaignAudience.mockResolvedValue({ segment_ids: [], lead_count: 0 });
    getCampaignEligibility.mockResolvedValue({ eligible: 0, skipped: {}, intents: [] });
    planCampaign.mockResolvedValue([]);
    getCampaignResults.mockResolvedValue({ metrics: {}, eligibility: {} });
    getCampaignKpis.mockResolvedValue({ total_leads: 0, attempted: 0, conversions: 0, dnc_skipped: 0, failed: 0 });

    render(<CampaignDetailPage campaignId="campaign-404" />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Campaign not found'));
    expect(screen.getByText('Campaign not found', { selector: '[data-testid="console-empty"] div' })).toBeInTheDocument();
  });
});
