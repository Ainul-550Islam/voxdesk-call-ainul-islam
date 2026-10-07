import { afterEach, describe, expect, it, vi } from 'vitest'
import { runCampaign } from '../lib/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('campaign run API client', () => {
  it('sends an explicit dry_run=false only for the confirmed live operation', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ dialed: 0, results: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await runCampaign('tenant-001', 'campaign-001', { dryRun: false })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tenants/tenant-001/campaigns/campaign-001/run?dry_run=false',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('keeps dry-run mode explicit as the API client default', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ dialed: 0, results: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await runCampaign('tenant-001', 'campaign-001')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tenants/tenant-001/campaigns/campaign-001/run?dry_run=true',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
