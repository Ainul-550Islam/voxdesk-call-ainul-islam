/**
 * dashboard/src/hooks/useAgentTemplates.ts
 *
 * Reusable starting points ("templates") for new agents.
 *
 * This system has no shipped template library and no `/api/agents/templates`
 * endpoint, so this hook does not pretend one exists. Templates are the
 * tenant's **own agents**, loaded from `GET /api/v1/agents` and offered as
 * starting points you can copy via the real clone endpoint
 * (`POST /api/v1/agents/{agent_id}/clone`).
 *
 * `hasLibraryTemplates` is therefore always false, and the UI is expected to
 * say "start from one of your agents" rather than implying a vendor catalog.
 * The previous version of this hook was a placeholder that set
 * `data = { agentId }` and never issued a request.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createAgentFromTemplate,
  listAgentTemplates,
  type AgentTemplate,
} from '../api/agent-templates';

export interface UseAgentTemplatesResult {
  templates: AgentTemplate[];
  total: number;
  /** Always false in this system; reported, not omitted. */
  hasLibraryTemplates: boolean;
  loading: boolean;
  error: string | null;
  creating: boolean;
  reload: () => Promise<void>;
  /** Clone one of the tenant's agents into a new agent. */
  useTemplate: (templateId: string, newName: string) => Promise<{ id: string; name: string } | null>;
}

export function useAgentTemplates(): UseAgentTemplatesResult {
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [total, setTotal] = useState(0);
  const [hasLibraryTemplates, setHasLibraryTemplates] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listAgentTemplates();
      if (!mounted.current) return;
      setTemplates(res.templates);
      setTotal(res.total);
      setHasLibraryTemplates(res.has_library_templates);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load templates');
      setTemplates([]);
      setTotal(0);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const useTemplate = useCallback(
    async (templateId: string, newName: string) => {
      setCreating(true);
      setError(null);
      try {
        const created = await createAgentFromTemplate(templateId, newName);
        if (!mounted.current) return created;
        await reload();
        return created;
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Failed to create agent from template');
        }
        return null;
      } finally {
        if (mounted.current) setCreating(false);
      }
    },
    [reload],
  );

  return {
    templates,
    total,
    hasLibraryTemplates,
    loading,
    error,
    creating,
    reload,
    useTemplate,
  };
}

export default useAgentTemplates;
