import React from 'react';
import { PublicWorkflowOverview } from '../shared/PublicWorkflowOverview';

export function AnalyticsPage() {
  return (
    <PublicWorkflowOverview
      eyebrow="Analytics overview"
      title="Review tenant-scoped operational data"
      summary="The authenticated analytics console reads backend aggregates for the signed-in workspace. This public overview contains no sample charts, fabricated rates, or customer metrics."
      metaTitle="Analytics overview | VoxDesk"
      metaDescription="VoxDesk analytics are displayed in the authenticated workspace from backend records. This public page shows no sample metrics or simulated results."
      workflowExamples={[
        {
          title: 'Inspect call aggregates',
          description: 'The operator analytics path requests server-computed call totals and series for the selected date range.',
        },
        {
          title: 'Review conversion definitions',
          description: 'Read each metric together with its denominator and selected business-timezone window.',
        },
        {
          title: 'Check usage separately',
          description: 'Billing-period usage and call-range analytics are distinct server responses and should not be treated as interchangeable.',
        },
      ]}
      operationalNote="Open the protected analytics route to retrieve real tenant data. A missing or empty record set is not replaced with a demo value. Provider reachability, completed call volume, and populated analytics depend on actual persisted activity."
      links={[
        { label: 'Open authenticated analytics', href: '/dashboard/analytics' },
        { label: 'Review calls', href: '/dashboard/calls' },
        { label: 'View pricing catalogue', href: '/pricing' },
      ]}
    />
  );
}

export default AnalyticsPage;
