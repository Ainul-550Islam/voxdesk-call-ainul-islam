import { useState, useEffect, useCallback } from 'react';
import {
  fetchAgentVersions,
  fetchAgentVersion,
  diffAgentVersions,
  rollbackToAgentVersion,
} from '../api/agent-versions';
import type {
  AgentVersionSnapshot,
  AgentVersionDiffResponse,
} from '../api/types/agent-version';

export function useAgentVersions(agentId?: string) {
  const [data, setData] = useState<AgentVersionSnapshot[]>([]);
  const [selectedVersion, setSelectedVersion] =
    useState<AgentVersionSnapshot | null>(null);
  const [diff, setDiff] = useState<AgentVersionDiffResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchAgentVersions(agentId);
      setData(rows);
    } catch (e: any) {
      setError(e?.message || 'Failed to load version history');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const inspectVersion = useCallback(
    async (versionNumber: number) => {
      if (!agentId) return null;
      const snap = await fetchAgentVersion(agentId, versionNumber);
      setSelectedVersion(snap);
      return snap;
    },
    [agentId]
  );

  const compareVersions = useCallback(
    async (fromVersion: number, toVersion: number) => {
      if (!agentId) return null;
      const d = await diffAgentVersions(agentId, fromVersion, toVersion);
      setDiff(d);
      return d;
    },
    [agentId]
  );

  const rollback = useCallback(
    async (targetVersion: number, reason = '') => {
      if (!agentId) return null;
      const ver = await rollbackToAgentVersion(agentId, {
        target_version: targetVersion,
        reason,
      });
      await reload();
      return ver;
    },
    [agentId, reload]
  );

  return {
    data,
    versions: data,
    selectedVersion,
    diff,
    loading,
    error,
    reload,
    inspectVersion,
    compareVersions,
    rollback,
  };
}
