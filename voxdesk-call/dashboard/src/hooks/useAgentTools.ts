/**
 * dashboard/src/hooks/useAgentTools.ts
 *
 * Registered custom tools (functions) for one agent.
 *
 * Backed by `app/api/tool_registry_routes.py` through `api/agent-tools.ts`.
 * The previous version of this hook was a placeholder: it set
 * `data = { agentId }` and never issued a request, so the tools screen could
 * never show a tool, an error, or a loading state.
 *
 * Scope note kept deliberate: these are the *registered custom functions* for
 * an agent. The built-in tool contracts the runtime dispatches live in
 * `app/agent/functions.py` and are read through `GET /api/agents/tools/catalog`
 * (see `api/agentCatalog.ts`). This hook never invents a tool.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createAgentTool,
  deleteAgentTool,
  disableAgentTool,
  enableAgentTool,
  listAgentTools,
  updateAgentTool,
  type AgentTool,
  type CreateAgentToolInput,
  type UpdateAgentToolInput,
} from '../api/agent-tools';

export interface UseAgentToolsResult {
  tools: AgentTool[];
  loading: boolean;
  error: string | null;
  saving: boolean;
  enabledCount: number;
  disabledCount: number;
  reload: () => Promise<void>;
  create: (input: CreateAgentToolInput) => Promise<AgentTool | null>;
  update: (toolId: string, input: UpdateAgentToolInput) => Promise<AgentTool | null>;
  remove: (toolId: string) => Promise<void>;
  setEnabled: (toolId: string, enabled: boolean) => Promise<void>;
}

export function useAgentTools(agentId?: string): UseAgentToolsResult {
  const [tools, setTools] = useState<AgentTool[]>([]);
  const [loading, setLoading] = useState(Boolean(agentId));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    if (!agentId) {
      setTools([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const rows = await listAgentTools(agentId);
      if (!mounted.current) return;
      setTools(rows);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load tools');
      setTools([]);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const create = useCallback(
    async (input: CreateAgentToolInput) => {
      if (!agentId) {
        setError('No agent selected');
        return null;
      }
      setSaving(true);
      setError(null);
      try {
        const created = await createAgentTool(agentId, input);
        if (!mounted.current) return created;
        setTools((current) => [...current.filter((tool) => tool.id !== created.id), created]);
        return created;
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Failed to create tool');
        }
        return null;
      } finally {
        if (mounted.current) setSaving(false);
      }
    },
    [agentId],
  );

  const update = useCallback(
    async (toolId: string, input: UpdateAgentToolInput) => {
      if (!agentId) {
        setError('No agent selected');
        return null;
      }
      setSaving(true);
      setError(null);
      try {
        const updated = await updateAgentTool(agentId, toolId, input);
        if (!mounted.current) return updated;
        setTools((current) => current.map((tool) => (tool.id === toolId ? updated : tool)));
        return updated;
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Failed to update tool');
        }
        return null;
      } finally {
        if (mounted.current) setSaving(false);
      }
    },
    [agentId],
  );

  const remove = useCallback(
    async (toolId: string) => {
      if (!agentId) {
        setError('No agent selected');
        return;
      }
      setSaving(true);
      setError(null);
      try {
        await deleteAgentTool(agentId, toolId);
        if (!mounted.current) return;
        setTools((current) => current.filter((tool) => tool.id !== toolId));
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Failed to delete tool');
        }
        throw err;
      } finally {
        if (mounted.current) setSaving(false);
      }
    },
    [agentId],
  );

  const setEnabled = useCallback(
    async (toolId: string, enabled: boolean) => {
      if (!agentId) {
        setError('No agent selected');
        return;
      }
      setSaving(true);
      setError(null);
      try {
        const updated = enabled
          ? await enableAgentTool(agentId, toolId)
          : await disableAgentTool(agentId, toolId);
        if (!mounted.current) return;
        setTools((current) =>
          current.map((tool) =>
            tool.id === toolId
              ? { ...tool, ...updated, is_enabled: updated?.is_enabled ?? enabled }
              : tool,
          ),
        );
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Failed to change tool state');
        }
        throw err;
      } finally {
        if (mounted.current) setSaving(false);
      }
    },
    [agentId],
  );

  return {
    tools,
    loading,
    error,
    saving,
    enabledCount: tools.filter((tool) => tool.is_enabled).length,
    disabledCount: tools.filter((tool) => !tool.is_enabled).length,
    reload,
    create,
    update,
    remove,
    setEnabled,
  };
}

export default useAgentTools;
