/** dashboard/src/api/agent-versions.ts — Durable Agent Version History, Snapshot & Diff API client */
import { apiClient } from './client';
import type {
  AgentVersionSnapshot,
  AgentVersionDiffResponse,
  RollbackAgentVersionInput,
} from './types/agent-version';

export async function getAgentVersions(agentId: string): Promise<AgentVersionSnapshot[]> {
  try {
    const res = await apiClient.get<any>(
      `/api/agents/${encodeURIComponent(agentId)}/versions`
    );
    return Array.isArray(res) ? res : res?.versions || res?.items || [];
  } catch {
    return [];
  }
}

export const fetchAgentVersions = getAgentVersions;

export async function listAgentVersions(agentId?: string): Promise<AgentVersionSnapshot[]> {
  if (!agentId) return [];
  return getAgentVersions(agentId);
}

export async function fetchAgentVersion(
  agentId: string,
  versionNumber: number
): Promise<AgentVersionSnapshot> {
  return apiClient.get<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(String(versionNumber))}`
  );
}

export const getAgentVersion = fetchAgentVersion;

export async function diffAgentVersions(
  agentId: string,
  fromVersion: number,
  toVersion: number
): Promise<AgentVersionDiffResponse> {
  return apiClient.get<AgentVersionDiffResponse>(
    `/api/agents/${encodeURIComponent(agentId)}/versions/diff?v1=${encodeURIComponent(String(fromVersion))}&v2=${encodeURIComponent(String(toVersion))}`
  );
}

export async function rollbackToAgentVersion(
  agentId: string,
  input: RollbackAgentVersionInput
): Promise<AgentVersionSnapshot> {
  const targetVersion = input.target_version ?? input.version ?? 1;
  return apiClient.post<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(agentId)}/rollback`,
    {
      target_version: targetVersion,
      reason: input.reason ?? '',
    }
  );
}
