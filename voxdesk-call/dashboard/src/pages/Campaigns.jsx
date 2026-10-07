/**
 * Outbound campaigns.
 *
 * Campaign records and lead counters are read from the tenant's selected
 * environment. A campaign run is a server-side operation: dry runs are the
 * safe default, while live dialing requires a separate confirmation and the
 * backend enforces tenant/environment scope, outbound policy, and billing
 * entitlement before contacting a provider.
 */
import { useState } from 'react'

import { Alert, AsyncSection, DataTable, Dialog, EmptyState, StatusBadge } from '../components/ui'
import { getUsageAnalytics, listCampaigns, runCampaign } from '../lib/api'
import { formatNumber, formatPercent } from '../lib/format'
import { useAction, useApi } from '../lib/hooks'
import { PERMISSIONS as P } from '../lib/permissions'

function campaignRunNotice(result, dryRun) {
  if (!result) {
    return { tone: 'error', message: 'The campaign-run API returned no response.' }
  }

  if (result.reason === 'campaign_inactive') {
    return { tone: 'warn', message: 'This campaign is inactive; no provider calls were placed.' }
  }
  if (result.reason === 'outside_window') {
    return { tone: 'warn', message: 'The tenant outbound call window is closed; no provider calls were placed.' }
  }

  const rows = Array.isArray(result.results) ? result.results : []
  if (dryRun) {
    const selected = rows.filter((row) => row?.dry_run === true).length
    const blocked = rows.filter((row) => row?.reason).length
    if (selected > 0) {
      const blockedNote = blocked > 0 ? ` ${blocked} other candidate(s) were not processed.` : ''
      return {
        tone: 'info',
        message: `Dry run processed ${formatNumber(selected)} eligible lead(s) without placing provider calls. Attempt state was recorded.${blockedNote}`,
      }
    }
    if (blocked > 0) {
      const reasons = [...new Set(rows.filter((row) => row?.reason).map((row) => row.reason))]
      return {
        tone: 'warn',
        message: `The dry run placed no provider calls. ${formatNumber(blocked)} candidate(s) were blocked: ${reasons.join(', ')}.`,
      }
    }
    return { tone: 'info', message: 'No leads were ready for the dry-run batch. No provider calls were placed.' }
  }

  const accepted = rows.filter((row) => Boolean(row?.call_sid)).length
  const billingDenials = rows.filter((row) => row?.reason === 'billing_entitlement_denied')
  const failed = rows.filter((row) => !row?.call_sid && row?.reason)
  if (accepted > 0) {
    const remaining = failed.length > 0
      ? ` ${formatNumber(failed.length)} other candidate(s) were not accepted by the provider.`
      : ''
    return {
      tone: failed.length > 0 ? 'warn' : 'ok',
      message: `The telephony provider accepted ${formatNumber(accepted)} live call request(s). Provider acceptance does not confirm that a recipient answered.${remaining}`,
    }
  }
  if (billingDenials.length > 0) {
    const reason = billingDenials[0].billing_reason || 'the current plan does not allow this call'
    return {
      tone: 'error',
      message: `The live batch was blocked by billing entitlement (${reason}); no provider call was accepted.`,
    }
  }
  if (failed.length > 0) {
    const reasons = [...new Set(failed.map((row) => row.reason))]
    return {
      tone: 'error',
      message: `No live call was accepted. ${formatNumber(failed.length)} candidate(s) failed: ${reasons.join(', ')}.`,
    }
  }
  return { tone: 'info', message: 'No leads were ready for the live batch. No provider calls were accepted.' }
}

export default function Campaigns({ me, can }) {
  const [flash, setFlash] = useState(null)
  const [confirmCampaign, setConfirmCampaign] = useState(null)

  const { data, error, loading, reload } = useApi(
    () => listCampaigns(me.tenant.id), []
  )
  const usage = useApi(
    () => getUsageAnalytics(), [], { skip: !can(P.BILLING_READ) }
  )

  const run = useAction(
    async (campaignId, dryRun) => ({
      result: await runCampaign(me.tenant.id, campaignId, { dryRun }),
      dryRun,
    }),
    {
      onSuccess: ({ result, dryRun }) => {
        setFlash(campaignRunNotice(result, dryRun))
        reload()
      },
    }
  )

  const voice = usage.data?.metrics?.find((metric) => metric.metric === 'voice_minute')

  const startLiveRun = () => {
    if (!confirmCampaign || run.pending) return
    const campaignId = confirmCampaign.id
    setConfirmCampaign(null)
    run.run(campaignId, false)
  }

  const columns = [
    { key: 'name', header: 'Campaign', render: (campaign) => campaign.name },
    {
      key: 'goal', header: 'Goal',
      render: (campaign) => <span className="muted">{campaign.goal}</span>,
    },
    {
      key: 'active', header: 'Status',
      render: (campaign) => (
        <StatusBadge
          status={campaign.is_active ? 'active' : 'paused'}
          label={campaign.is_active ? 'Active' : 'Paused'}
        />
      ),
    },
    {
      key: 'leads', header: 'Leads', numeric: true,
      render: (campaign) => formatNumber(campaign.leads_total ?? 0),
    },
    {
      key: 'processed', header: 'Processed', numeric: true,
      render: (campaign) => formatNumber(campaign.leads_done ?? 0),
    },
    {
      key: 'actions', header: 'Actions',
      render: (campaign) => can(P.CAMPAIGN_RUN) ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn btn--small"
            disabled={run.pending || !campaign.is_active}
            onClick={() => run.run(campaign.id, true)}
            title={campaign.is_active ? 'Process a dry-run batch without contacting the telephony provider' : 'Activate the campaign before running it'}
          >
            Run dry-run batch
          </button>
          <button
            type="button"
            className="btn btn--small"
            disabled={run.pending || !campaign.is_active}
            onClick={() => setConfirmCampaign(campaign)}
            title={campaign.is_active ? 'Review and confirm a live outbound batch' : 'Activate the campaign before running it'}
          >
            Place live batch
          </button>
        </div>
      ) : <span className="muted">—</span>,
    },
  ]

  const dialogFooter = (
    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
      <button
        type="button"
        className="btn btn--small"
        onClick={() => setConfirmCampaign(null)}
        disabled={run.pending}
      >
        Cancel
      </button>
      <button
        type="button"
        className="btn btn--small"
        onClick={startLiveRun}
        disabled={run.pending}
      >
        Confirm live calls
      </button>
    </div>
  )

  return (
    <>
      <div className="page__header">
        <div>
          <h1>Campaigns</h1>
          <p className="page__description">
            Outbound calling batches. Campaign records and lead counters come from
            the selected tenant environment; live dialing is an explicit, separately
            confirmed operation.
          </p>
        </div>
      </div>

      {flash && (
        <Alert tone={flash.tone} onDismiss={() => setFlash(null)}>
          {flash.message}
        </Alert>
      )}
      {run.error && (
        <Alert tone="error" onDismiss={run.clearError}>
          {run.error.message}
        </Alert>
      )}

      {voice && (
        <Alert tone={voice.overage > 0 ? 'warn' : 'info'}>
          You have used {formatNumber(voice.used)} of{' '}
          {formatNumber(voice.included)} included voice minutes this period
          ({formatPercent(voice.percent_used)}).
          {voice.overage > 0 && (
            <> Further calls are billed as overage, or refused if your plan does
            not allow it.</>
          )}
        </Alert>
      )}

      <div className="card">
        <AsyncSection
          loading={loading}
          error={error}
          onRetry={reload}
          resource="campaigns"
          isEmpty={data && data.length === 0}
          empty={
            <EmptyState
              icon="➤"
              title="No campaigns"
              description="Campaigns are created through the API. Once one exists in this tenant environment it will appear here."
            />
          }
        >
          {data && (
            <DataTable
              caption="Campaigns"
              columns={columns}
              rows={data}
              keyOf={(campaign) => campaign.id}
            />
          )}
        </AsyncSection>
      </div>

      <Dialog
        open={Boolean(confirmCampaign)}
        title="Confirm live outbound batch"
        onClose={() => setConfirmCampaign(null)}
        footer={dialogFooter}
      >
        {confirmCampaign && (
          <div className="stack">
            <p>You are about to request a real outbound batch for campaign <strong>{confirmCampaign.name}</strong>.</p>
            <p>Eligible leads may be called and carrier charges may apply. VoxDesk checks tenant and environment scope, the outbound setting, consent and do-not-call rules, the tenant call window, and the current billing entitlement before attempting the provider call.</p>
            <p>This dialog is not a provider preflight. Live execution still requires valid telephony credentials and provider availability. Provider acceptance is not proof that a recipient answered.</p>
          </div>
        )}
      </Dialog>
    </>
  )
}
