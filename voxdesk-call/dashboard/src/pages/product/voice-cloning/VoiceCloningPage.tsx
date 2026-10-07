import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function VoiceCloningPage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Voice configuration overview"
      title="Review voice-provider configuration"
      summary="Voice choices and synthesis depend on the providers enabled for a tenant and deployment. This public page does not present sample recordings or claim that a voice clone has been created."
      metaTitle="Voice configuration overview | VoxDesk"
      metaDescription="Voice provider configuration is deployment-specific. No cloned voice, latency result, or speaker-consent verification is represented on this page."
      workflowExamples={[
        {
          title: 'Select a configured voice provider',
          description: 'The authenticated agent configuration surfaces should report the options available to the current deployment.',
        },
        {
          title: 'Review model and voice settings',
          description: 'A selected setting is not evidence of successful synthesis. Verify an actual provider operation before relying on it.',
        },
        {
          title: 'Check rights and consent requirements',
          description: 'Voice cloning and use of a person’s voice require appropriate rights, consent, and legal review; this page does not attest that these checks occurred.',
        },
      ]}
      operationalNote="No cloned voice, synthetic audio sample, speaker-consent record, latency measurement, or provider success is shown here. Availability depends on the configured voice provider and the exact account and deployment permissions."
      links={[
        { label: 'Open agent workspace', href: '/dashboard/agents' },
        { label: 'Read developer documentation', href: '/docs' },
        { label: 'Browse provider adapters', href: '/integrations' },
      ]}
    />
  );
}

export default VoiceCloningPage;
