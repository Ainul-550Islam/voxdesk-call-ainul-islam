/** dashboard/src/api/agents.ts — Durable Agent & Version API client */
import { apiClient, ApiError } from './client';
import type {
  Agent,
  AgentCreateRequest,
  AgentUpdateRequest,
  AgentTemplate,
  AgentListResponse,
  DurableAgentRecord,
  AgentValidationResult,
  AgentTestResult,
} from './types/agent';
import { getModelProviders as getModelCatalog } from './agent-models';
import { getVoiceProviders as getVoiceCatalog } from './agent-voices';
import type {
  AgentVersionSnapshot,
  PublishAgentVersionInput,
  RollbackAgentVersionInput,
} from './types/agent-version';

function normalizeAgent(raw: any): DurableAgentRecord {
  if (!raw || typeof raw !== 'object') {
    return {
      id: '',
      tenant_id: '',
      name: 'Agent',
      status: 'DRAFT',
      type: 'VOICE',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
  const rawStatus = String(raw.status || 'DRAFT').toUpperCase();
  const normalizedStatus = (
    ['DRAFT', 'VALIDATING', 'VALID', 'INVALID', 'PUBLISHED', 'UNPUBLISHED', 'ARCHIVED', 'ERROR'].includes(
      rawStatus
    )
      ? rawStatus
      : rawStatus === 'RETIRED'
      ? 'ARCHIVED'
      : 'DRAFT'
  ) as DurableAgentRecord['status'];

  return {
    ...raw,
    id: String(raw.id || raw.agent_id || raw.external_key || ''),
    tenant_id: String(raw.tenant_id || ''),
    name: String(raw.name || raw.identity?.name || 'Agent'),
    description: String(raw.description || raw.identity?.description || ''),
    status: normalizedStatus,
    type: (String(raw.type || raw.agent_type || 'VOICE').toUpperCase() as DurableAgentRecord['type']),
    language: String(raw.language || raw.primary_language || raw.voice?.language || 'en-US'),
    system_prompt: String(raw.system_prompt || raw.model?.system_prompt || ''),
    version: Number(raw.published_version_number ?? raw.active_version ?? raw.version ?? 1),
    etag: String(raw.draft_etag || raw.etag || ''),
    draft_etag: String(raw.draft_etag || raw.etag || ''),
    created_at: String(raw.created_at || raw.updated_at || new Date().toISOString()),
    updated_at: String(raw.updated_at || new Date().toISOString()),
  };
}

export async function listAgents(params?: {
  search?: string;
  status?: string;
  sort?: string;
  page?: number;
}): Promise<AgentListResponse> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set('search', params.search);
  if (params?.status && params.status !== 'all') qs.set('status', params.status);
  if (params?.sort) qs.set('sort', params.sort);
  if (params?.page) qs.set('page', String(params.page));
  const q = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<any>(`/api/agents${q}`);
  const rawList = Array.isArray(res) ? res : res?.agents || res?.data || [];
  const agents = rawList.map(normalizeAgent);
  return {
    agents,
    total: typeof res?.total === 'number' ? res.total : agents.length,
  };
}

export async function fetchAgents(): Promise<DurableAgentRecord[]> {
  const res = await listAgents();
  return res.agents;
}

export async function getAgent(id: string): Promise<DurableAgentRecord> {
  const res = await apiClient.get<any>(`/api/agents/${encodeURIComponent(id)}`);
  return normalizeAgent(res?.agent || res);
}

export const fetchAgent = getAgent;

export async function createAgent(payload: AgentCreateRequest | Record<string, unknown>): Promise<DurableAgentRecord> {
  const res = await apiClient.post<any>('/api/agents', payload);
  return normalizeAgent(res?.agent || res);
}

export async function updateAgent(
  id: string,
  payload: AgentUpdateRequest & { expected_etag?: string }
): Promise<DurableAgentRecord> {
  const headers: Record<string, string> = {};
  if (payload.expected_etag) {
    headers['If-Match'] = payload.expected_etag;
  }
  const res = await apiClient.put<any>(`/api/agents/${encodeURIComponent(id)}`, payload, {
    headers,
  });
  return normalizeAgent(res?.agent || res);
}

export async function deleteAgent(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/agents/${encodeURIComponent(id)}`);
}

export async function cloneAgent(id: string, newName?: string): Promise<DurableAgentRecord> {
  const res = await apiClient.post<any>(`/api/v1/agents/${encodeURIComponent(id)}/clone`, {
    new_name: newName || `Agent Copy ${id.slice(0, 6)}`,
    include_knowledge_bases: true,
    include_tools: true,
  });
  return normalizeAgent({
    id: res?.new_agent_id || res?.id || id,
    name: res?.name || newName || 'Cloned Agent',
    status: res?.status || 'draft',
    created_at: res?.created_at || new Date().toISOString(),
    updated_at: res?.created_at || new Date().toISOString(),
  });
}

export async function archiveAgent(id: string, reason = ''): Promise<DurableAgentRecord> {
  const res = await apiClient.post<any>(`/api/v1/agents/${encodeURIComponent(id)}/archive`, {
    reason,
  });
  return normalizeAgent(res?.agent || res);
}

export async function restoreAgent(id: string): Promise<DurableAgentRecord> {
  const res = await apiClient.post<any>(`/api/v1/agents/${encodeURIComponent(id)}/restore`, {});
  return normalizeAgent(res?.agent || res);
}

export async function validateAgentConfig(id: string): Promise<AgentValidationResult> {
  return apiClient.post<AgentValidationResult>(
    `/api/agents/${encodeURIComponent(id)}/validate`,
    {}
  );
}

export async function publishAgentConfig(
  id: string,
  input: PublishAgentVersionInput = {}
): Promise<AgentVersionSnapshot> {
  return apiClient.post<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(id)}/publish`,
    {
      changelog: input.changelog ?? input.release_notes ?? '',
      environment: input.environment ?? 'production',
    }
  );
}

export async function fetchAgentVersionHistory(id: string): Promise<AgentVersionSnapshot[]> {
  const res = await apiClient.get<any>(`/api/agents/${encodeURIComponent(id)}/versions`);
  return Array.isArray(res) ? res : res?.versions || [];
}

export async function fetchAgentVersionSnapshot(
  id: string,
  versionNumber: number
): Promise<AgentVersionSnapshot> {
  return apiClient.get<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(String(versionNumber))}`
  );
}

export async function rollbackAgentToVersion(
  id: string,
  input: RollbackAgentVersionInput
): Promise<AgentVersionSnapshot> {
  const targetVersion = input.target_version ?? input.version ?? 1;
  return apiClient.post<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(id)}/rollback`,
    {
      target_version: targetVersion,
      reason: input.reason ?? '',
    }
  );
}

export async function testAgent(
  id: string,
  sampleUtterance?: string
): Promise<AgentTestResult> {
  return apiClient.post<AgentTestResult>(
    `/api/agents/${encodeURIComponent(id)}/test`,
    sampleUtterance ? { sample_utterance: sampleUtterance } : {}
  );
}

export async function getTemplates(): Promise<AgentTemplate[]> {
  // This system ships no template library and has no `/api/agents/templates`
  // endpoint. Rather than return `[]` from a 404, templates are derived from
  // the tenant's own agents — see `api/agent-templates.ts`.
  // Imported lazily on purpose: `agent-templates.ts` derives templates from
  // this module's `listAgents`, so a static import would be a cycle.
  const { listAgentTemplates } = await import('./agent-templates');
  const res = await listAgentTemplates();
  return res.templates as unknown as AgentTemplate[];
}

export async function getVoiceProviders(): Promise<any[]> {
  // Delegates to the typed catalog client. `GET /api/agents/voices` is served
  // by `app/api/agent_catalog_routes.py`; previously this called a path that
  // did not exist and swallowed the 404 into `[]`, which is why the voice
  // picker in `VoiceConfigPanel` rendered empty.
  const catalog = await getVoiceCatalog();
  return catalog.providers;
}

export async function getModelProviders(): Promise<any[]> {
  // Delegates to the typed catalog client. `GET /api/agents/models` is served
  // by `app/api/agent_catalog_routes.py`; previously this called a path that
  // did not exist and swallowed the 404 into `[]`, which is why the model
  // picker in `ModelConfigPanel` rendered empty.
  const catalog = await getModelCatalog();
  return catalog.providers;
}

export { ApiError };
export type { DurableAgentRecord };
