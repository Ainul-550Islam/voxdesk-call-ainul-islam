import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function InboundPage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Inbound calling overview"
      title="Plan an inbound voice workflow"
      summary="Explore the steps that may be involved in an inbound call. This page does not claim that a number is provisioned, a provider is connected, or that a caller will be answered on a particular schedule."
      metaTitle="Inbound calling workflow ideas | VoxDesk"
      metaDescription="Illustrative inbound voice workflows for VoxDesk. Live call handling depends on number assignment, agent version, provider setup, and deployment state."
      workflowExamples={[
        {
          title: 'Assign a number and agent',
          description: 'The authenticated phone-number console exposes number and agent-binding workflows; availability and provider acceptance must be checked there.',
        },
        {
          title: 'Receive a provider event',
          description: 'Inbound behavior depends on the configured carrier webhook and the exact tenant, environment, number, and agent version.',
        },
        {
          title: 'Inspect call state',
          description: 'Review a persisted call record and its pinned version; a route or UI action is not proof that a call connected.',
        },
      ]}
      operationalNote="Live inbound calling requires a configured telephony provider, valid number and webhook setup, a published agent in the selected environment, and carrier connectivity. Provider credentials and live-call outcomes are not tested on this public page."
      links={[
        { label: 'Open phone-number console', href: '/dashboard/phone-numbers' },
        { label: 'Review calls', href: '/dashboard/calls' },
        { label: 'Browse telephony adapters', href: '/integrations' },
      ]}
    />
  );
}

export default InboundPage;
