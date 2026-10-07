import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function AnsweringServicePage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Call-answering workflow overview"
      title="Explore call-answering patterns"
      summary="This page outlines possible ways a voice agent could handle an incoming inquiry. It does not promise 24/7 coverage, an answer-time SLA, a calendar booking, or a connected CRM."
      metaTitle="Call-answering workflow ideas | VoxDesk"
      metaDescription="Illustrative call-answering patterns for VoxDesk. Live availability, booking, routing, and provider connections depend on deployment configuration."
      workflowExamples={[
        {
          title: 'Capture the caller’s request',
          description: 'A configured voice workflow may collect an inquiry and direct the caller to an appropriate next step.',
        },
        {
          title: 'Route or request a handoff',
          description: 'A transfer depends on a supported destination, call state, operator policy, and telephony-provider behavior.',
        },
        {
          title: 'Review the persisted call',
          description: 'Use the authenticated call console to inspect records returned by the backend; this public page contains no sample calls.',
        },
      ]}
      operationalNote="A real inbound answering flow requires an assigned phone number, an eligible agent/version, an inbound webhook or provider connection, and working deployment credentials. Calendar, SMS, CRM, and recording behavior must be verified separately for the tenant."
      links={[
        { label: 'Open phone-number console', href: '/dashboard/phone-numbers' },
        { label: 'Review calls', href: '/dashboard/calls' },
        { label: 'Explore adapter inventory', href: '/integrations' },
      ]}
    />
  );
}

export default AnsweringServicePage;
