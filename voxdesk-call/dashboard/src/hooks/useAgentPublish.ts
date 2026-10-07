import { useState, useEffect, useCallback } from 'react';
import {
  getAgentPublish,
  listAgentPublish,
  publishAgentBuilder,
  rollbackBuilderVersion,
  promoteAgentEnvironment,
} from '../api/agent-publish';
import type { PublishAgentVersionInput } from '../api/types/agent-version';

export function useAgentPublish(id?: string) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const [envState, versions] = await Promise.all([
        getAgentPublish(id),
        listAgentPublish(id),
      ]);
      setData({ environments: envState, versions });
    } catch (e: any) {
      setError(e?.message || 'Error loading publish state');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    reload();
  }, [reload]);

  const publish = useCallback(
    async (input: PublishAgentVersionInput = {}) => {
      if (!id) return null;
      const res = await publishAgentBuilder(id, input);
      await reload();
      return res;
    },
    [id, reload]
  );

  const rollback = useCallback(
    async (targetVersion: number, reason = '') => {
      if (!id) return null;
      const res = await rollbackBuilderVersion(id, targetVersion, reason);
      await reload();
      return res;
    },
    [id, reload]
  );

  const promote = useCallback(
    async (
      fromEnv: 'draft' | 'staging',
      toEnv: 'staging' | 'production',
      changelog = ''
    ) => {
      if (!id) return null;
      const res = await promoteAgentEnvironment(id, fromEnv, toEnv, changelog);
      await reload();
      return res;
    },
    [id, reload]
  );

  return {
    data,
    loading,
    error,
    reload,
    publish,
    rollback,
    promote,
  };
}
