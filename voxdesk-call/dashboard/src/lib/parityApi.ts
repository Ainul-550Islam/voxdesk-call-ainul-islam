import { apiClient } from '../api/client';

export type ParityStatus =
  | 'MISSING'
  | 'PARTIAL'
  | 'IMPLEMENTED'
  | 'VERIFIED'
  | 'PRODUCTION_READY'
  | 'NOT_CONFIGURED'
  | 'RESOURCE_LIMITED';

export type ConnectionStatus =
  | 'CONNECTED'
  | 'NOT_CONFIGURED'
  | 'AUTH_REQUIRED'
  | 'ERROR'
  | 'DISABLED'
  | 'SANDBOX'
  | 'UNVERIFIED';

export type InspectionStatus =
  | 'PASS'
  | 'FAIL'
  | 'PARTIAL'
  | 'NOT_RUN'
  | 'NOT_CONFIGURED'
  | 'RESOURCE_LIMITED';

export interface RouteEvidence {
  method: string;
  path: string;
  module: string;
}

export interface CapabilityItem {
  key: string;
  label: string;
  status: ParityStatus;
  summary: string;
  evidence_routes: RouteEvidence[];
  evidence_basis: 'registered_routes_only';
}

export interface CapabilityInventory {
  generated_at: string;
  registered_api_operations: number;
  suppressed_generated_placeholder_routes: number;
  suppressed_generic_placeholder_routes: number;
  capabilities: CapabilityItem[];
  limitation: string;
}

export interface IntegrationStatusItem {
  integration_type: string;
  provider: string;
  status: ConnectionStatus;
  configured: boolean;
  enabled: boolean | null;
  credentials_present: boolean;
  last_health_check_at: string | null;
  last_health_ok: boolean | null;
  status_basis: string;
}

export interface IntegrationInventory {
  tenant_id: string;
  generated_at: string;
  items: IntegrationStatusItem[];
  limitation: string;
}

export interface E2EInspectionStep {
  key: string;
  label: string;
  status: InspectionStatus;
  detail: string;
}

export interface E2EInspection {
  tenant_id: string;
  environment_id: string | null;
  agent_id: string;
  agent_name: string;
  agent_status: string;
  published_version_number: number | null;
  published_version_id: string | null;
  call_id: string | null;
  call_status: string | null;
  call_agent_version_number: number | null;
  call_agent_version_id: string | null;
  is_simulation: boolean | null;
  overall_status: InspectionStatus;
  steps: E2EInspectionStep[];
  side_effects_performed: false;
  limitation: string;
}

export async function getCapabilityInventory(): Promise<CapabilityInventory> {
  // apiClient prefixes the configured API base URL (default: /api).
  return apiClient.get<CapabilityInventory>('/v1/parity/capabilities');
}

export async function getIntegrationInventory(): Promise<IntegrationInventory> {
  return apiClient.get<IntegrationInventory>('/v1/parity/integrations');
}

export async function inspectE2EFlow(
  agentId: string,
  callId?: string,
  environmentId?: string,
): Promise<E2EInspection> {
  const query = new URLSearchParams({ agent_id: agentId.trim() });
  if (callId?.trim()) query.set('call_id', callId.trim());
  if (environmentId?.trim()) query.set('environment_id', environmentId.trim());
  return apiClient.get<E2EInspection>(
    `/v1/parity/e2e/inspect?${query.toString()}`,
  );
}

export const parityApi = {
  getCapabilityInventory,
  getIntegrationInventory,
  inspectE2EFlow,
};
