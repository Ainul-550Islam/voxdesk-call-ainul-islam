import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function CustomerServicePage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Customer-service workflow overview"
      title="Plan customer-service conversations"
      summary="This overview describes possible voice, chat, and messaging workflows without claiming that every channel or external service is active in a particular VoxDesk deployment."
      metaTitle="Customer-service workflow ideas | VoxDesk"
      metaDescription="Illustrative customer-service workflows for VoxDesk. Channel availability, provider delivery, and human handoff depend on configuration."
      workflowExamples={[
        {
          title: 'Answer a product question',
          description: 'An agent may use a configured knowledge source; retrieved answers and indexing status must be checked in the authenticated workspace.',
        },
        {
          title: 'Escalate to a person',
          description: 'A handoff requires an authorized target and supported live-call or messaging path. No handoff is performed by this public page.',
        },
        {
          title: 'Continue across channels',
          description: 'Voice, chat, and SMS have separate runtime, provider, and consent requirements. No cross-channel delivery is implied.',
        },
      ]}
      operationalNote="The repository contains voice, chat, and messaging surfaces with different implementation and configuration boundaries. A route, channel record, or UI control alone does not prove a provider delivered a message or that a customer conversation completed."
      links={[
        { label: 'Open chat-agent workspace', href: '/dashboard/chat-agents' },
        { label: 'Review call records', href: '/dashboard/calls' },
        { label: 'Browse use-case examples', href: '/use-cases' },
      ]}
    />
  );
}

export default CustomerServicePage;
