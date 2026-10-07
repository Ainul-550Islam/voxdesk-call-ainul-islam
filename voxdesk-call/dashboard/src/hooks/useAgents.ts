import { useEffect, useState, useMemo, useCallback } from 'react';
import { listAgents } from '../api/agents';
import type { Agent } from '../types/agent';

export function useAgents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listAgents({
        search: search || undefined,
        status: statusFilter,
      });
      setAgents(res.agents);
    } catch (e: any) {
      setError(e?.message || 'Failed to load agents');
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter]);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const filtered = useMemo(() => {
    if (!search && statusFilter === 'all') return agents;
    return agents.filter((a) => {
      const matchSearch =
        !search || a.name.toLowerCase().includes(search.toLowerCase());
      const matchStatus =
        statusFilter === 'all' || a.status === statusFilter;
      return matchSearch && matchStatus;
    });
  }, [agents, search, statusFilter]);

  const total = agents.length;
  const published = agents.filter((a) => a.status === 'PUBLISHED').length;
  const draft = agents.filter((a) => a.status === 'DRAFT').length;
  const retry = () => fetchAgents();

  return {
    agents: filtered,
    allAgents: agents,
    loading,
    error,
    search,
    setSearch,
    statusFilter,
    setStatusFilter,
    retry,
    total,
    published,
    draft,
  };
}
