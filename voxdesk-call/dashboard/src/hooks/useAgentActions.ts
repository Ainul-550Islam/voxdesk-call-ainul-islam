import { useState, useEffect, useCallback } from 'react';
import {
  getAgentActions,
  listAgentActions,
  archiveAgent,
  restoreAgent,
  deleteAgent,
  duplicateAgent,
} from '../api/agent-actions';
import type { DurableAgentRecord } from '../api/types/agent';

export function useAgentActions(id?: string) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = id ? await getAgentActions(id) : await listAgentActions();
      setData(res);
    } catch (e: any) {
      setError(e?.message || 'Error loading agent actions');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const archive = useCallback(
    async (agentId: string, reason = '') => {
      const res = await archiveAgent(agentId, reason);
      await load();
      return res;
    },
    [load]
  );

  const restore = useCallback(
    async (agentId: string) => {
      const res = await restoreAgent(agentId);
      await load();
      return res;
    },
    [load]
  );

  const remove = useCallback(
    async (agentId: string) => {
      const res = await deleteAgent(agentId);
      await load();
      return res;
    },
    [load]
  );

  const duplicate = useCallback(
    async (sourceAgent: DurableAgentRecord, name?: string) => {
      const res = await duplicateAgent(sourceAgent, { name });
      await load();
      return res;
    },
    [load]
  );

  return {
    data,
    loading,
    error,
    reload: load,
    archive,
    restore,
    remove,
    duplicate,
  };
}
