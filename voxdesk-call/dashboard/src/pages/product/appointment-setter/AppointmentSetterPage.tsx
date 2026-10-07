import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function AppointmentSetterPage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Scheduling workflow overview"
      title="Explore appointment workflow patterns"
      summary="Appointment intake, availability checks, booking, and reminders are shown here as workflow concepts. The page does not assert that an external calendar or messaging provider is connected."
      metaTitle="Appointment workflow ideas | VoxDesk"
      metaDescription="Illustrative appointment workflows for VoxDesk. Calendar, booking, and reminder behavior is deployment-specific and is not demonstrated on this page."
      workflowExamples={[
        {
          title: 'Collect scheduling requirements',
          description: 'A workflow can ask for a preferred time, service, and contact details before a human or configured integration reviews the request.',
        },
        {
          title: 'Check an external calendar',
          description: 'Availability requires a tenant-configured calendar adapter with valid credentials and a successful provider operation.',
        },
        {
          title: 'Confirm or reschedule',
          description: 'A confirmation, calendar write, or reminder should be treated as complete only after the external operation and persisted outcome are verified.',
        },
      ]}
      operationalNote="Calendar adapters are represented in the backend provider registry, but tenant credentials, provider health, calendar permissions, and booking results are not established by this public overview. SMS and email delivery are separate provider-dependent operations."
      links={[
        { label: 'Browse calendar adapters', href: '/integrations?category=calendar' },
        { label: 'Open agent workspace', href: '/dashboard/agents' },
        { label: 'Read developer documentation', href: '/docs' },
      ]}
    />
  );
}

export default AppointmentSetterPage;
