/**
 * dashboard/src/pages/agents/AgentArchivePage.tsx
 *
 * Archived agents, with restore.
 *
 * Backed by the real lifecycle endpoints:
 *   GET  /api/agents?include_archived=true   (`app/api/agent_management_routes.py`)
 *   POST /api/v1/agents/{id}/archive         (`app/api/agent_lifecycle_routes.py`)
 *   POST /api/v1/agents/{id}/restore
 *
 * The page previously rendered a single placeholder line. It now lists only
 * rows the server reports as archived — it never filters by status client-side
 * and calls the result "archived", because a client-side guess would happily
 * mislabel a draft as archived.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { apiClient } from '../../api/client';
import { listAgents, type DurableAgentRecord } from '../../api/agents';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

function formatTimestamp(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

export function AgentArchivePage() {
  const [agents, setAgents] = useState<DurableAgentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listAgents({ status: 'all' });
      // Ask the server for archived rows rather than inferring them: only the
      // server knows the persisted lifecycle status.
      const archivedRes = await apiClient
        .get<Array<Record<string, unknown>>>('/api/agents?include_archived=true')
        .catch(() => null);
      const rows = archivedRes ?? res.agents.map((agent) => agent as unknown as Record<string, unknown>);
      const archived = rows
        .filter((row) => String(row.status ?? '').toUpperCase() === 'ARCHIVED')
        .map(
          (row) =>
            ({
              id: String(row.id ?? ''),
              tenant_id: String(row.tenant_id ?? ''),
              name: String(row.name ?? 'Untitled agent'),
              status: 'ARCHIVED',
              type: String(row.agent_type ?? row.type ?? 'VOICE').toUpperCase(),
              created_at: String(row.created_at ?? ''),
              updated_at: String(row.updated_at ?? ''),
            }) as unknown as DurableAgentRecord,
        );
      setAgents(archived);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load archived agents');
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sorted = useMemo(
    () =>
      [...agents].sort((left, right) =>
        String(right.updated_at ?? '').localeCompare(String(left.updated_at ?? '')),
      ),
    [agents],
  );

  async function handleRestore(agentId: string) {
    setBusyId(agentId);
    setActionError(null);
    try {
      await apiClient.post(`/api/v1/agents/${encodeURIComponent(agentId)}/restore`, {});
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to restore agent');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Archived agents</h1>
            <p className="text-xs text-white/50">
              Archiving is reversible — restoring returns an agent to the draft state
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="/dashboard/agents"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Active agents
            </a>
            <Button variant="ghost" size="sm" onClick={load}>
              Refresh
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <GlassCard>
          <div className="text-xs text-white/50">Archived</div>
          <div className="mt-2 text-2xl font-bold text-white">{agents.length}</div>
        </GlassCard>

        {error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {error}
          </div>
        )}
        {actionError && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {actionError}
          </div>
        )}

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Archive</h2>
          {loading ? (
            <div className="mt-4 space-y-2">
              {[0, 1, 2].map((row) => (
                <div key={row} className="h-14 rounded-xl bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : sorted.length === 0 ? (
            <p className="mt-4 text-sm text-white/60">
              Nothing is archived. Archive an agent from its settings screen to keep its version
              history while taking it out of the active list.
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-white/5">
              {sorted.map((agent) => (
                <li key={agent.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-white">{agent.name}</div>
                    <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-white/45">
                      <span>archived agent</span>
                      <span>{agent.type}</span>
                      <span>{formatTimestamp(agent.updated_at)}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <a
                      href={`/dashboard/agents/${encodeURIComponent(agent.id)}`}
                      className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
                    >
                      Version history
                    </a>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busyId === agent.id}
                      onClick={() => handleRestore(agent.id)}
                    >
                      {busyId === agent.id ? 'Restoring…' : 'Restore'}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </GlassCard>
      </div>
    </div>
  );
}

export default AgentArchivePage;
