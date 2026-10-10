/** dashboard/src/api/agent-builder.ts — Durable Agent Builder API client */
import { apiClient } from './client';
import type {
  BuilderConfig,
  ValidationResult,
  PublishResult,
  Version,
} from '../types/agent-builder';

function mapBuilderPayloadToConfig(res: any, fallbackAgentId = ''): BuilderConfig {
  const a = res?.config || res || {};
  const identity = a.identity || {};
  const voice = a.voice || {};
  const model = a.model || {};
  const rawStatus = String(a.status || 'DRAFT').toUpperCase();
  return {
    id: String(a.id || a.agent_id || fallbackAgentId),
    tenant_id: String(a.tenant_id || ''),
    name: String(identity.name || a.name || 'Agent'),
    description: String(identity.description || a.description || ''),
    status: rawStatus,
    type: String(a.agent_type || a.type || 'VOICE').toUpperCase(),
    language: String(voice.language || a.primary_language || a.language || 'en-US'),
    voice_id: String(voice.voice_id || a.voice_id || 'rachel'),
    model_id: String(model.model_name || model.model || a.llm_model || a.model_id || 'gpt-4o'),
    system_prompt: String(model.system_prompt || a.system_prompt || ''),
    config: {
      identity,
      voice,
      model,
      knowledge_bases: a.knowledge_bases || [],
      tools: a.tools || [],
      call_handling: a.call_handling || {},
      security: a.security || {},
    },
    version: Number(res?.version ?? a.published_version ?? a.version ?? a.active_version ?? 1),
    etag: String(res?.draft_etag || res?.etag || a.draft_etag || a.etag || ''),
    updated_at: String(res?.updated_at || a.updated_at || new Date().toISOString()),
  };
}

export async function getBuilderConfig(agentId: string): Promise<BuilderConfig> {
  try {
    const res = await apiClient.get<any>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/builder`
    );
    return mapBuilderPayloadToConfig(res, agentId);
  } catch {
    const fallback = await apiClient.get<any>(`/api/agents/${encodeURIComponent(agentId)}`);
    return mapBuilderPayloadToConfig(fallback, agentId);
  }
}

export const fetchAgentBuilderConfig = getBuilderConfig;

export async function updateBuilderConfig(
  agentId: string,
  config: Partial<BuilderConfig>,
  etag?: string
): Promise<BuilderConfig> {
  const headers: Record<string, string> = {};
  const expectedEtag = etag || config.etag;
  if (expectedEtag) {
    headers['If-Match'] = expectedEtag;
  }

  const nested = (config.config || {}) as Record<string, any>;
  const patchBody: Record<string, any> = {
    identity: {
      name: config.name || nested.identity?.name || 'Agent',
      description: config.description ?? nested.identity?.description ?? '',
      persona: nested.identity?.persona || 'You are a helpful AI assistant.',
      greeting: nested.identity?.greeting || 'Hello, how can I help you today?',
      end_call_message:
        nested.identity?.end_call_message || 'Thank you for calling. Goodbye!',
      fallback_message:
        nested.identity?.fallback_message ||
        "I'm sorry, I didn't catch that. Could you repeat?",
    },
    voice: {
      provider: nested.voice?.provider || 'elevenlabs',
      voice_id: config.voice_id || nested.voice?.voice_id || 'rachel',
      language: config.language || nested.voice?.language || 'en-US',
      speed: Number(nested.voice?.speed ?? 1.0),
      pitch: Number(nested.voice?.pitch ?? 1.0),
      stability: Number(nested.voice?.stability ?? 0.75),
      similarity_boost: Number(nested.voice?.similarity_boost ?? 0.75),
      fallback_voice_id: nested.voice?.fallback_voice_id ?? null,
    },
    model: {
      provider: nested.model?.provider || 'openai',
      model_name: config.model_id || nested.model?.model_name || 'gpt-4o',
      temperature: Number(nested.model?.temperature ?? 0.3),
      max_tokens: Number(nested.model?.max_tokens ?? 512),
      system_prompt:
        config.system_prompt ??
        nested.model?.system_prompt ??
        'You are a helpful AI assistant.',
      context_window_turns: Number(nested.model?.context_window_turns ?? 20),
      response_style: nested.model?.response_style || 'conversational',
    },
  };
  if (Array.isArray(nested.knowledge_bases)) {
    patchBody.knowledge_bases = nested.knowledge_bases;
  }
  if (Array.isArray(nested.tools)) {
    patchBody.tools = nested.tools;
  }
  if (nested.call_handling && typeof nested.call_handling === 'object') {
    patchBody.call_handling = nested.call_handling;
  }
  if (nested.security && typeof nested.security === 'object') {
    patchBody.security = nested.security;
  }
  if (expectedEtag) {
    patchBody.expected_etag = expectedEtag;
  }

  const res = await apiClient.patch<any>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/builder`,
    patchBody,
    { headers }
  );
  return mapBuilderPayloadToConfig(res, agentId);
}

export const saveAgentBuilderConfig = updateBuilderConfig;

export async function validateAgent(agentId: string): Promise<ValidationResult> {
  try {
    const res = await apiClient.post<any>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/validate`,
      {}
    );
    return {
      valid: Boolean(res?.valid),
      errors: Array.isArray(res?.errors)
        ? res.errors.map((e: any) => ({
            field: String(e.field || 'config'),
            message: String(e.message || 'Validation error'),
            severity: 'error' as const,
            code: e.code,
          }))
        : [],
      warnings: Array.isArray(res?.warnings)
        ? res.warnings.map((w: any) => ({
            field: String(w.field || 'config'),
            message: String(w.message || 'Validation warning'),
            severity: 'warning' as const,
            code: w.code,
          }))
        : [],
      validated_at: res?.checked_at || new Date().toISOString(),
    };
  } catch (e: any) {
    return {
      valid: false,
      errors: [
        {
          field: 'general',
          message: e?.message || 'Validation failed',
          severity: 'error',
        },
      ],
      warnings: [],
    };
  }
}

export const validateAgentBuilderConfig = validateAgent;

export async function publishAgent(
  agentId: string,
  options?: { release_notes?: string; changelog?: string; environment?: string }
): Promise<PublishResult> {
  const res = await apiClient.post<any>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/publish`,
    {
      release_notes: options?.release_notes || options?.changelog || '',
      changelog: options?.changelog || options?.release_notes || '',
      environment: options?.environment || 'production',
    }
  );
  return {
    success: true,
    agent_id: String(res?.agent_id || agentId),
    version: Number(res?.version || 1),
    published_at: String(res?.published_at || new Date().toISOString()),
  };
}

export async function unpublishAgent(agentId: string): Promise<void> {
  await apiClient.post(`/api/v1/agents/${encodeURIComponent(agentId)}/archive`, {
    reason: 'unpublished',
  });
}

export async function getVersions(agentId: string): Promise<Version[]> {
  const res = await apiClient.get<any>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/versions`
  );
  const items = Array.isArray(res) ? res : res?.versions || [];
  return items.map((v: any, idx: number) => ({
    id: String(v.id || `${agentId}-v${v.version ?? v.version_number ?? idx + 1}`),
    version: Number(v.version ?? v.version_number ?? idx + 1),
    created_at: String(v.published_at || v.created_at || new Date().toISOString()),
    author: String(v.published_by || 'system'),
    status: String(v.status || 'published'),
    changes: String(v.changelog || v.release_notes || 'Published snapshot'),
    is_current: v.is_active !== undefined ? Boolean(v.is_active) : idx === 0,
    etag: String(v.config_hash || ''),
    config_hash: String(v.config_hash || ''),
    config_snapshot: v.config_snapshot || {},
    published_environment: String(v.published_environment || 'production'),
    is_rollback: Boolean(v.is_rollback),
  }));
}

export async function getVersion(agentId: string, versionNumber: number): Promise<any> {
  return apiClient.get<any>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(String(versionNumber))}`
  );
}

export const getVersionSnapshot = getVersion;

export async function rollbackVersion(
  agentId: string,
  versionNumber: number,
  reason = ''
): Promise<any> {
  return apiClient.post<any>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/rollback`,
    {
      version: versionNumber,
      reason,
    }
  );
}

export async function getKnowledge(agentId: string): Promise<any[]> {
  const cfg = await getBuilderConfig(agentId);
  const kbs = (cfg.config as any)?.knowledge_bases;
  return Array.isArray(kbs) ? kbs : [];
}

export async function attachKnowledge(agentId: string, kbId: string): Promise<void> {
  const cfg = await getBuilderConfig(agentId);
  const existing = Array.isArray((cfg.config as any)?.knowledge_bases)
    ? [...(cfg.config as any).knowledge_bases]
    : [];
  if (!existing.some((k: any) => k.kb_id === kbId)) {
    existing.push({
      kb_id: kbId,
      name: `Knowledge Base ${kbId}`,
      priority: existing.length + 1,
      top_k: 3,
      similarity_threshold: 0.7,
    });
  }
  await updateBuilderConfig(
    agentId,
    {
      ...cfg,
      config: { ...(cfg.config || {}), knowledge_bases: existing },
    },
    cfg.etag
  );
}

export async function detachKnowledge(agentId: string, kbId: string): Promise<void> {
  const cfg = await getBuilderConfig(agentId);
  const existing = Array.isArray((cfg.config as any)?.knowledge_bases)
    ? (cfg.config as any).knowledge_bases.filter((k: any) => k.kb_id !== kbId)
    : [];
  await updateBuilderConfig(
    agentId,
    {
      ...cfg,
      config: { ...(cfg.config || {}), knowledge_bases: existing },
    },
    cfg.etag
  );
}

export async function getTools(agentId: string): Promise<any[]> {
  const cfg = await getBuilderConfig(agentId);
  const tools = (cfg.config as any)?.tools;
  return Array.isArray(tools) ? tools : [];
}

export async function attachTool(agentId: string, toolId: string): Promise<void> {
  const cfg = await getBuilderConfig(agentId);
  const existing = Array.isArray((cfg.config as any)?.tools)
    ? [...(cfg.config as any).tools]
    : [];
  if (!existing.some((t: any) => t.tool_id === toolId)) {
    existing.push({
      tool_id: toolId,
      name: toolId,
      description: `Enabled tool ${toolId}`,
      http_method: 'POST',
      timeout_ms: 5000,
      retry_count: 1,
      parameters: [],
      enabled: true,
    });
  }
  await updateBuilderConfig(
    agentId,
    {
      ...cfg,
      config: { ...(cfg.config || {}), tools: existing },
    },
    cfg.etag
  );
}

export async function detachTool(agentId: string, toolId: string): Promise<void> {
  const cfg = await getBuilderConfig(agentId);
  const existing = Array.isArray((cfg.config as any)?.tools)
    ? (cfg.config as any).tools.filter((t: any) => t.tool_id !== toolId)
    : [];
  await updateBuilderConfig(
    agentId,
    {
      ...cfg,
      config: { ...(cfg.config || {}), tools: existing },
    },
    cfg.etag
  );
}
