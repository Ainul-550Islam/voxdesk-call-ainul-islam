/**
 * dashboard/src/hooks/useAgentMetrics.ts
 *
 * Per-agent **configuration** metrics.
 *
 * Backed by `api/agent-metrics.ts`, which composes four real endpoints
 * (`/api/agents/{id}/versions`, `/api/agents/{id}/tools`,
 * `/api/knowledge/stats`, `/api/v1/agents/{id}/validate`).
 *
 * The previous version of this hook was a placeholder: it set
 * `data = { agentId }` and never issued a request, so a dashboard could render
 * "0 versions, 0 tools" for an agent that had many.
 *
 * What this is NOT: call analytics. There is no per-agent call-volume or
 * latency endpoint in this system, so none is claimed —
 * `includesCallAnalytics` is always false and the UI must not render usage
 * numbers from this hook.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { getAgentMetrics, type AgentConfigurationMetrics } from '../api/agent-metrics';

export interface UseAgentMetricsResult {
  metrics: AgentConfigurationMetrics | null;
  loading: boolean;
  error: string | null;
  /** False by contract: these are configuration metrics, not call analytics. */
  includesCallAnalytics: boolean;
  reload: () => Promise<void>;
}

export function useAgentMetrics(agentId?: string): UseAgentMetricsResult {
  const [metrics, setMetrics] = useState<AgentConfigurationMetrics | null>(null);
  const [loading, setLoading] = useState(Boolean(agentId));
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    if (!agentId) {
      setMetrics(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await getAgentMetrics(agentId);
      if (!mounted.current) return;
      setMetrics(next);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load agent metrics');
      setMetrics(null);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    metrics,
    loading,
    error,
    includesCallAnalytics: metrics?.includes_call_analytics ?? false,
    reload,
  };
}

export default useAgentMetrics;
