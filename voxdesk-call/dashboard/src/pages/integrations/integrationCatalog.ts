export type IntegrationFamily = 'telephony' | 'crm' | 'calendar';

export interface IntegrationDirectoryEntry {
  id: string;
  name: string;
  family: IntegrationFamily;
  registryEvidence: string;
  discoveryPath: string;
  accessBoundary: string;
  description: string;
}

/**
 * Repository-level provider identifiers only. These entries are not a claim
 * that an account is configured, a vendor contract exists, or a live operation
 * has succeeded. The identifiers mirror the backend enum/factory source files.
 */
export const INTEGRATION_DIRECTORY: IntegrationDirectoryEntry[] = [
  {
    id: 'twilio',
    name: 'Twilio',
    family: 'telephony',
    registryEvidence: 'app/telephony/providers/factory.py',
    discoveryPath: 'Telephony adapter factory',
    accessBoundary: 'Tenant binding and deployment credentials are checked separately.',
    description: 'The telephony adapter factory recognizes this provider identifier. Number provisioning and live calls still depend on valid account configuration and provider responses.',
  },
  {
    id: 'telnyx',
    name: 'Telnyx',
    family: 'telephony',
    registryEvidence: 'app/telephony/providers/factory.py',
    discoveryPath: 'Telephony adapter factory',
    accessBoundary: 'Tenant binding and deployment credentials are checked separately.',
    description: 'The telephony adapter factory recognizes this provider identifier. Number provisioning and live calls still depend on valid account configuration and provider responses.',
  },
  {
    id: 'vonage',
    name: 'Vonage',
    family: 'telephony',
    registryEvidence: 'app/telephony/providers/factory.py',
    discoveryPath: 'Telephony adapter factory',
    accessBoundary: 'Tenant binding and deployment credentials are checked separately.',
    description: 'The telephony adapter factory recognizes this provider identifier. Number provisioning and live calls still depend on valid account configuration and provider responses.',
  },
  {
    id: 'gohighlevel',
    name: 'GoHighLevel',
    family: 'crm',
    registryEvidence: 'CrmProviderType and CRM capability registry',
    discoveryPath: 'GET /api/integrations/crm/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The generic CRM provider enum includes this identifier. The authenticated provider catalogue returns its declared capabilities; a tenant connection requires separate credentials and a health check.',
  },
  {
    id: 'hubspot',
    name: 'HubSpot',
    family: 'crm',
    registryEvidence: 'CrmProviderType and CRM capability registry',
    discoveryPath: 'GET /api/integrations/crm/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The generic CRM provider enum includes this identifier. The authenticated provider catalogue returns its declared capabilities; a tenant connection requires separate credentials and a health check.',
  },
  {
    id: 'jobber',
    name: 'Jobber',
    family: 'crm',
    registryEvidence: 'CrmProviderType and CRM capability registry',
    discoveryPath: 'GET /api/integrations/crm/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The generic CRM provider enum includes this identifier. The authenticated provider catalogue returns its declared capabilities; a tenant connection requires separate credentials and a health check.',
  },
  {
    id: 'webhook',
    name: 'CRM webhook adapter',
    family: 'crm',
    registryEvidence: 'CrmProviderType and CRM webhook adapter',
    discoveryPath: 'GET /api/integrations/crm/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The generic CRM provider enum includes a webhook adapter. Delivery semantics and downstream behavior depend on its specific configuration and verified operation.',
  },
  {
    id: 'google',
    name: 'Google Calendar',
    family: 'calendar',
    registryEvidence: 'CalendarProviderType and calendar adapter registry',
    discoveryPath: 'GET /api/calendar/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The calendar provider enum includes this identifier. OAuth credentials, calendar access, and a confirmed provider write are separate deployment-specific checks.',
  },
  {
    id: 'google_service_account',
    name: 'Google Calendar service account',
    family: 'calendar',
    registryEvidence: 'CalendarProviderType and calendar adapter registry',
    discoveryPath: 'GET /api/calendar/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The calendar provider enum retains a service-account mode distinct from per-tenant Google OAuth. Credential scope and access must be reviewed for the deployment.',
  },
  {
    id: 'microsoft',
    name: 'Microsoft Calendar',
    family: 'calendar',
    registryEvidence: 'CalendarProviderType and calendar adapter registry',
    discoveryPath: 'GET /api/calendar/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The calendar provider enum includes this identifier. Credentials, calendar permissions, and booking outcomes are not established by this public directory.',
  },
  {
    id: 'calcom',
    name: 'Cal.com',
    family: 'calendar',
    registryEvidence: 'CalendarProviderType and calendar adapter registry',
    discoveryPath: 'GET /api/calendar/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The calendar provider enum includes this identifier. A tenant-specific connection and successful scheduling operation require separate verification.',
  },
  {
    id: 'internal',
    name: 'Internal calendar adapter',
    family: 'calendar',
    registryEvidence: 'CalendarProviderType and calendar adapter registry',
    discoveryPath: 'GET /api/calendar/providers',
    accessBoundary: 'Authenticated · integration:read',
    description: 'The calendar provider enum includes an internal adapter. It is not an external vendor connection and should not be interpreted as one.',
  },
];

export const INTEGRATION_FAMILIES: Array<{ id: 'all' | IntegrationFamily; label: string }> = [
  { id: 'all', label: 'All registry entries' },
  { id: 'telephony', label: 'Telephony adapters' },
  { id: 'crm', label: 'CRM adapters' },
  { id: 'calendar', label: 'Calendar adapters' },
];
