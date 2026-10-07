/**
 * dashboard/src/api/agent-metrics.ts
 *
 * Per-agent **configuration** metrics.
 *
 * The previous version of this file called `/api/agents/{id}/agent-metrics`
 * and `/api/agent-metrics`, neither of which exists, and returned `null` /
 * `[]` — a fabricated "no data" state.
 *
 * This version composes metrics from endpoints that do exist, and is explicit
 * about what it is *not*: there are no call-volume or latency numbers here,
 * because no per-agent call-analytics endpoint exists in this system. The
 * fields below describe how completely an agent is configured, which is what
 * the Studio can actually measure:
 *
 *   GET  /api/agents/{id}/versions   -> immutable version history
 *   GET  /api/agents/{id}/tools      -> registered custom functions
 *   GET  /api/knowledge/stats        -> tenant knowledge-base totals
 *   POST /api/v1/agents/{id}/validate-> typed validation issues
 */

import { apiClient } from './client';
import { listAgentTools } from './agent-tools';
import { validateAgentConfig } from './agent-validation';

export interface AgentConfigurationMetrics {
  agent_id: string;
  /** Number of immutable versions ever published for this agent. */
  version_count: number;
  /** The newest published version number, or null when nothing is published. */
  latest_version: number | null;
  /** Registered custom functions. */
  tool_count: number;
  enabled_tool_count: number;
  /** Tenant-wide knowledge-base totals (documents, chunks, characters). */
  knowledge: Record<string, unknown> | null;
  /** Validation result at the time of the call. */
  validation: {
    valid: boolean;
    error_count: number;
    warning_count: number;
    checked_at: string;
  } | null;
  /**
   * Always false. No per-agent call analytics endpoint exists; this field is
   * reported rather than omitted so a dashboard cannot imply otherwise.
   */
  includes_call_analytics: boolean;
  measured_at: string;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export async function getAgentMetrics(agentId: string): Promise<AgentConfigurationMetrics> {
  const encoded = encodeURIComponent(agentId);

  const [versionsRes, toolsRes, knowledgeRes, validationRes] = await Promise.allSettled([
    apiClient.get<unknown>(`/api/agents/${encoded}/versions`),
    listAgentTools(agentId),
    apiClient.get<Record<string, unknown>>('/api/knowledge/stats'),
    validateAgentConfig(agentId),
  ]);

  const versions = versionsRes.status === 'fulfilled' ? asArray(versionsRes.value) : [];
  const versionNumbers = versions
    .map((entry) => {
      if (entry && typeof entry === 'object') {
        const raw = (entry as Record<string, unknown>).version_number;
        const fallback = (entry as Record<string, unknown>).version;
        const value = typeof raw === 'number' ? raw : typeof fallback === 'number' ? fallback : null;
        return value;
      }
      return null;
    })
    .filter((value): value is number => typeof value === 'number');

  const tools = toolsRes.status === 'fulfilled' ? toolsRes.value : [];
  const knowledge =
    knowledgeRes.status === 'fulfilled' ? (knowledgeRes.value as Record<string, unknown>) : null;
  const validation = validationRes.status === 'fulfilled' ? validationRes.value : null;

  return {
    agent_id: agentId,
    version_count: versionNumbers.length,
    latest_version: versionNumbers.length ? Math.max(...versionNumbers) : null,
    tool_count: tools.length,
    enabled_tool_count: tools.filter((tool) => tool.is_enabled).length,
    knowledge,
    validation: validation
      ? {
          valid: validation.valid,
          error_count: validation.errors.length,
          warning_count: validation.warnings.length,
          checked_at: validation.checked_at,
        }
      : null,
    includes_call_analytics: false,
    measured_at: new Date().toISOString(),
  };
}

export async function listAgentMetrics(agentIds: string[]): Promise<AgentConfigurationMetrics[]> {
  const results = await Promise.allSettled(agentIds.map((id) => getAgentMetrics(id)));
  return results
    .filter((result): result is PromiseFulfilledResult<AgentConfigurationMetrics> => result.status === 'fulfilled')
    .map((result) => result.value);
}
