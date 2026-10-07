import { apiClient } from './client';
import type {
  ConductorApplyPayload,
  ConductorApplyResult,
  ConductorChange,
  ConductorEvidence,
  ConductorPromptPayload,
  ConductorProposal,
  ConductorProposalDiff,
  ConductorSession,
  ConductorSessionCreatePayload,
  ConductorSimulatePayload,
} from './types/conductor';

export async function createConductorSession(
  payload: ConductorSessionCreatePayload
): Promise<ConductorSession> {
  return apiClient.post<ConductorSession>('/api/v1/conductor/sessions', payload);
}

export async function listConductorSessions(params?: {
  agent_id?: string;
  limit?: number;
}): Promise<ConductorSession[]> {
  const qs = new URLSearchParams();
  if (params?.agent_id) qs.set('agent_id', params.agent_id);
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<ConductorSession[]>(
    `/api/v1/conductor/sessions${suffix}`
  );
  return Array.isArray(res) ? res : [];
}

export async function getConductorSession(
  sessionId: string
): Promise<ConductorSession> {
  return apiClient.get<ConductorSession>(
    `/api/v1/conductor/sessions/${encodeURIComponent(sessionId)}`
  );
}

export async function getConductorContext(params: {
  agent_id: string;
  agent_kind?: 'voice' | 'chat';
  base_version_number?: number;
}): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams({ agent_id: params.agent_id });
  if (params.agent_kind) qs.set('agent_kind', params.agent_kind);
  if (params.base_version_number !== undefined) {
    qs.set('base_version_number', String(params.base_version_number));
  }
  return apiClient.get<Record<string, unknown>>(
    `/api/v1/conductor/context?${qs.toString()}`
  );
}

export async function submitConductorRequest(
  payload: ConductorPromptPayload
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    '/api/v1/conductor/proposals',
    payload
  );
}

export async function listConductorProposals(params?: {
  agent_id?: string;
  session_id?: string;
  limit?: number;
}): Promise<ConductorProposal[]> {
  const qs = new URLSearchParams();
  if (params?.agent_id) qs.set('agent_id', params.agent_id);
  if (params?.session_id) qs.set('session_id', params.session_id);
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<ConductorProposal[]>(
    `/api/v1/conductor/proposals${suffix}`
  );
  return Array.isArray(res) ? res : [];
}

export async function getConductorProposal(
  proposalId: string
): Promise<ConductorProposal> {
  return apiClient.get<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}`
  );
}

export async function getProposalChanges(
  proposalId: string
): Promise<ConductorChange[]> {
  const res = await apiClient.get<ConductorChange[]>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/changes`
  );
  return Array.isArray(res) ? res : [];
}

export async function getProposalDiff(
  proposalId: string
): Promise<ConductorProposalDiff> {
  return apiClient.get<ConductorProposalDiff>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/diff`
  );
}

export async function listProposalEvidence(
  proposalId: string
): Promise<ConductorEvidence[]> {
  const res = await apiClient.get<ConductorEvidence[]>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/evidence`
  );
  return Array.isArray(res) ? res : [];
}

export async function validateProposal(
  proposalId: string
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/validate`,
    {}
  );
}

export async function simulateProposal(
  proposalId: string,
  payload?: ConductorSimulatePayload
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/simulate`,
    payload || {}
  );
}

export async function approveChange(
  proposalId: string,
  changeId: string,
  reason = ''
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(
      proposalId
    )}/changes/${encodeURIComponent(changeId)}/approve`,
    { reason }
  );
}

export async function rejectChange(
  proposalId: string,
  changeId: string,
  reason = ''
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(
      proposalId
    )}/changes/${encodeURIComponent(changeId)}/reject`,
    { reason }
  );
}

export async function undoChangeApproval(
  proposalId: string,
  changeId: string,
  reason = ''
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(
      proposalId
    )}/changes/${encodeURIComponent(changeId)}/undo`,
    { reason }
  );
}

export async function approveProposal(
  proposalId: string,
  options?: { reason?: string; safe_only?: boolean }
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/approve`,
    {
      reason: options?.reason || '',
      safe_only: options?.safe_only ?? false,
    }
  );
}

export async function rejectProposal(
  proposalId: string,
  reason = ''
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/reject`,
    { reason }
  );
}

export async function undoProposalApproval(
  proposalId: string,
  reason = ''
): Promise<ConductorProposal> {
  return apiClient.post<ConductorProposal>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/undo`,
    { reason }
  );
}

export async function applyProposal(
  proposalId: string,
  payload?: ConductorApplyPayload
): Promise<ConductorApplyResult> {
  return apiClient.post<ConductorApplyResult>(
    `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/apply`,
    payload || {}
  );
}

export async function getResultingVersion(
  proposalId: string
): Promise<Record<string, unknown>> {
  return apiClient.get<Record<string, unknown>>(
    `/api/v1/conductor/proposals/${encodeURIComponent(
      proposalId
    )}/resulting-version`
  );
}

export const conductorApi = {
  createConductorSession,
  listConductorSessions,
  getConductorSession,
  getConductorContext,
  submitConductorRequest,
  listConductorProposals,
  getConductorProposal,
  getProposalChanges,
  getProposalDiff,
  listProposalEvidence,
  validateProposal,
  simulateProposal,
  approveChange,
  rejectChange,
  undoChangeApproval,
  approveProposal,
  rejectProposal,
  undoProposalApproval,
  applyProposal,
  getResultingVersion,
};
