/**
 * dashboard/src/api/public-keys.ts
 * Authenticated tenant-scoped API client for PublicWidgetKey management (/api/v1/public-keys).
 */

import { client as api } from './client';
import type {
  PublicWidgetKeyCreateInput,
  PublicWidgetKeyCreatedResponse,
  PublicWidgetKeyRecord,
  PublicWidgetKeyStatus,
  PublicWidgetKeyUpdateInput,
} from './types/public-widget';

export async function listPublicWidgetKeys(params?: {
  agentId?: string;
  status?: PublicWidgetKeyStatus;
}): Promise<{ keys: PublicWidgetKeyRecord[]; total: number }> {
  const qs = new URLSearchParams();
  if (params?.agentId) qs.set('agent_id', params.agentId);
  if (params?.status) qs.set('status', params.status);
  const query = qs.toString() ? `?${qs.toString()}` : '';
  return api.get<{ keys: PublicWidgetKeyRecord[]; total: number }>(
    `/api/v1/public-keys${query}`,
  );
}

export async function getPublicWidgetKey(
  keyId: string,
): Promise<PublicWidgetKeyRecord> {
  return api.get<PublicWidgetKeyRecord>(
    `/api/v1/public-keys/${encodeURIComponent(keyId)}`,
  );
}

export async function createPublicWidgetKey(
  payload: PublicWidgetKeyCreateInput,
): Promise<PublicWidgetKeyCreatedResponse> {
  return api.post<PublicWidgetKeyCreatedResponse>('/api/v1/public-keys', payload);
}

export async function updatePublicWidgetKey(
  keyId: string,
  payload: PublicWidgetKeyUpdateInput,
): Promise<PublicWidgetKeyRecord> {
  return api.patch<PublicWidgetKeyRecord>(
    `/api/v1/public-keys/${encodeURIComponent(keyId)}`,
    payload,
  );
}

export async function rotatePublicWidgetKey(
  keyId: string,
  reason = 'Manual key rotation',
): Promise<PublicWidgetKeyCreatedResponse> {
  return api.post<PublicWidgetKeyCreatedResponse>(
    `/api/v1/public-keys/${encodeURIComponent(keyId)}/rotate`,
    { reason },
  );
}

export async function revokePublicWidgetKey(
  keyId: string,
  reason = 'Manual key revocation',
): Promise<PublicWidgetKeyRecord> {
  return api.post<PublicWidgetKeyRecord>(
    `/api/v1/public-keys/${encodeURIComponent(keyId)}/revoke`,
    { reason },
  );
}
