import React from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Campaigns from '../pages/Campaigns'
import { getUsageAnalytics, listCampaigns, runCampaign } from '../lib/api'

vi.mock('../lib/api', () => ({
  getUsageAnalytics: vi.fn(),
  listCampaigns: vi.fn(),
  runCampaign: vi.fn(),
}))

const CAMPAIGN = {
  id: 'campaign-001',
  name: 'Tenant campaign',
  goal: 'qualify',
  is_active: true,
  calls_per_minute: 2,
  environment_id: 'environment-001',
  leads_total: 7,
  leads_done: 3,
}

function renderCampaigns() {
  return render(
    <Campaigns
      me={{ tenant: { id: 'tenant-001' } }}
      can={() => true}
    />,
  )
}

describe('campaigns page uses persisted backend counters and explicit run modes', () => {
  beforeEach(() => {
    vi.mocked(listCampaigns).mockResolvedValue([CAMPAIGN])
    vi.mocked(getUsageAnalytics).mockResolvedValue({ metrics: [] })
    vi.mocked(runCampaign).mockResolvedValue({
      dialed: 2,
      results: [
        { ok: true, dry_run: true, to: '+15550000001' },
        { ok: true, dry_run: true, to: '+15550000002' },
      ],
    })
  })

  it('renders the backend lead_total and processed counters instead of missing legacy fields', async () => {
    renderCampaigns()

    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /Tenant campaign/ })
    expect(within(row).getByText('7')).toBeInTheDocument()
    expect(within(row).getByText('3')).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Processed' })).toBeInTheDocument()
  })

  it('marks the dry-run request explicitly and does not claim that calls were placed', async () => {
    const user = userEvent.setup()
    renderCampaigns()

    await user.click(await screen.findByRole('button', { name: 'Run dry-run batch' }))

    expect(runCampaign).toHaveBeenCalledWith(
      'tenant-001',
      'campaign-001',
      { dryRun: true },
    )
    expect(await screen.findByText(/Dry run processed 2 eligible lead\(s\) without placing provider calls/)).toBeInTheDocument()
    expect(screen.getByText(/Attempt state was recorded/)).toBeInTheDocument()
  })

  it('requires a separate confirmation before requesting a live batch', async () => {
    const user = userEvent.setup()
    vi.mocked(runCampaign).mockResolvedValue({
      dialed: 1,
      results: [{ ok: true, call_sid: 'provider-call-001', to: '+15550000001' }],
    })
    renderCampaigns()

    await user.click(await screen.findByRole('button', { name: 'Place live batch' }))
    const dialog = screen.getByRole('dialog', { name: 'Confirm live outbound batch' })
    expect(within(dialog).getByText(/carrier charges may apply/)).toBeInTheDocument()
    expect(runCampaign).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: 'Confirm live calls' }))

    expect(runCampaign).toHaveBeenCalledWith(
      'tenant-001',
      'campaign-001',
      { dryRun: false },
    )
    expect(await screen.findByText(/The telephony provider accepted 1 live call request/)).toBeInTheDocument()
    expect(screen.getByText(/does not confirm that a recipient answered/)).toBeInTheDocument()
  })
})
