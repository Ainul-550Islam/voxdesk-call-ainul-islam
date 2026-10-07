import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function OutboundPage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Outbound calling overview"
      title="Keep outbound calling under operator control"
      summary="Campaign records and dry-run actions are distinct from a provider-accepted live call. This page does not assert call volume, carrier connectivity, or a compliance certification."
      metaTitle="Outbound calling workflow ideas | VoxDesk"
      metaDescription="Illustrative outbound workflow patterns for VoxDesk. Live dialing depends on tenant settings, consent, billing enforcement, and telephony configuration."
      workflowExamples={[
        {
          title: 'Review the saved audience',
          description: 'Use the authenticated campaign console to inspect persisted campaign and lead records for the selected tenant and environment.',
        },
        {
          title: 'Run a dry-run operation',
          description: 'Dry-run behavior is not a live carrier call; an eligible batch can still record attempt state in the campaign environment.',
        },
        {
          title: 'Confirm live dialing explicitly',
          description: 'A live batch requires explicit operator confirmation, applicable policy checks, billing conditions, and a configured provider.',
        },
      ]}
      operationalNote="The campaign console distinguishes dry-run from live dialing. Live operation also depends on consent and do-not-call rules, tenant call windows, outbound enablement, provider credentials, and carrier availability. See the telemarketing page for the current operation boundaries."
      links={[
        { label: 'Read outbound campaign controls', href: '/product/telemarketing' },
        { label: 'Open campaign console', href: '/campaigns' },
        { label: 'Review call records', href: '/dashboard/calls' },
      ]}
    />
  );
}

export default OutboundPage;
