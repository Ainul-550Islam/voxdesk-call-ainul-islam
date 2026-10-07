import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function VoiceAgentsPage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Voice-agent product overview"
      title="Build and inspect voice-agent workflows"
      summary="The authenticated workspace contains route surfaces for agent creation, builder and version views, simulations, phone-number configuration, and call review. Route presence does not prove that a provider is configured or that a live call has succeeded."
      metaTitle="Voice-agent workflows | VoxDesk"
      metaDescription="Explore voice-agent workflow concepts and authenticated VoxDesk workspace routes. Live provider status and call outcomes are deployment-specific."
      workflowExamples={[
        {
          title: 'Configure an agent',
          description: 'Create an agent draft and review its saved configuration and published version in the authenticated workspace.',
        },
        {
          title: 'Test before deployment',
          description: 'Use the simulation and test surfaces to distinguish a test run from a paid production phone call.',
        },
        {
          title: 'Connect a phone number',
          description: 'Phone-number and SIP configuration is tenant- and deployment-specific; no carrier connection is asserted here.',
        },
        {
          title: 'Review persisted calls',
          description: 'Inspect the call record and version pinned to it rather than relying on a static preview or sample transcript.',
        },
      ]}
      operationalNote="Live calling depends on a tenant's published agent, environment and number binding, provider credentials, carrier availability, and applicable policy checks. This page makes no latency, uptime, customer-result, certification, or production-readiness claim."
      links={[
        { label: 'Open agent workspace', href: '/dashboard/agents' },
        { label: 'View testing surfaces', href: '/dashboard/simulations' },
        { label: 'View phone-number console', href: '/dashboard/phone-numbers' },
      ]}
    />
  );
}

export default VoiceAgentsPage;
