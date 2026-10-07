/**
 * dashboard/src/pages/agents/AgentListPage.tsx
 *
 * Table view of the tenant's agents.
 *
 * `AgentsPage` is the card wall; this is the dense operator table. It composes
 * the three list hooks that used to be placeholders:
 *
 *   useAgentFilters  — status / type / language filters, options derived from
 *                      the loaded rows so a new type is never unfilterable
 *   useAgentSearch   — client-side search over name / description / id
 *   useAgentSort     — column sorting; records missing the sort key sort last
 *
 * Every row links into the same durable surfaces as the card wall (detail +
 * versions, builder). Counts are computed from the real rows, never from a
 * hardcoded total.
 */

import React, { useMemo } from 'react';
import { useAgents } from '../../hooks/useAgents';
import { useAgentFilters } from '../../hooks/useAgentFilters';
import { useAgentSearch } from '../../hooks/useAgentSearch';
import { useAgentSort, type AgentSortKey } from '../../hooks/useAgentSort';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

const COLUMNS: Array<{ key: AgentSortKey | null; label: string; className?: string }> = [
  { key: 'name', label: 'Agent' },
  { key: 'status', label: 'Status', className: 'w-32' },
  { key: 'version', label: 'Version', className: 'w-24' },
  { key: null, label: 'Language', className: 'w-32' },
  { key: 'updated_at', label: 'Updated', className: 'w-48' },
  { key: null, label: '', className: 'w-56' },
];

const STATUS_STYLES: Record<string, string> = {
  PUBLISHED: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  DRAFT: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  ARCHIVED: 'bg-white/5 text-white/50 border-white/10',
  VALIDATING: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
  VALID: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  INVALID: 'bg-red-500/15 text-red-300 border-red-500/30',
  ERROR: 'bg-red-500/15 text-red-300 border-red-500/30',
};

function formatTimestamp(value: string | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

export function AgentListPage() {
  const { agents, loading, error, retry } = useAgents();
  const filters = useAgentFilters(agents);
  const search = useAgentSearch(filters.filtered);
  const sort = useAgentSort(search.results);

  const counts = useMemo(
    () => ({
      total: agents.length,
      published: agents.filter((agent) => String(agent.status).toUpperCase() === 'PUBLISHED').length,
      draft: agents.filter((agent) => String(agent.status).toUpperCase() === 'DRAFT').length,
      archived: agents.filter((agent) => String(agent.status).toUpperCase() === 'ARCHIVED').length,
    }),
    [agents],
  );

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">All agents</h1>
            <p className="text-xs text-white/50">
              Durable, tenant-scoped agents with immutable version history
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href="/dashboard/agents"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Card view
            </a>
            <a
              href="/dashboard/agents/archive"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Archive
            </a>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                window.location.href = '/dashboard/agents/new';
              }}
            >
              Create Agent
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-8 grid gap-4 sm:grid-cols-4">
          <GlassCard>
            <div className="text-xs text-white/50">Total</div>
            <div className="mt-2 text-2xl font-bold text-white">{counts.total}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Published</div>
            <div className="mt-2 text-2xl font-bold text-emerald-300">{counts.published}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Drafts</div>
            <div className="mt-2 text-2xl font-bold text-amber-300">{counts.draft}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Archived</div>
            <div className="mt-2 text-2xl font-bold text-white/60">{counts.archived}</div>
          </GlassCard>
        </div>

        <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-center">
          <input
            value={search.query}
            onChange={(event) => search.setQuery(event.target.value)}
            placeholder="Search by name, description, or id..."
            aria-label="Search agents"
            className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
          />
          <select
            value={filters.filters.status}
            onChange={(event) => filters.setFilter('status', event.target.value)}
            aria-label="Filter by status"
            className="rounded-xl border border-white/10 bg-black/50 px-4 py-2.5 text-sm text-white"
          >
            <option value="all">All statuses</option>
            {filters.options.statuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
          <select
            value={filters.filters.agentType}
            onChange={(event) => filters.setFilter('agentType', event.target.value)}
            aria-label="Filter by agent type"
            className="rounded-xl border border-white/10 bg-black/50 px-4 py-2.5 text-sm text-white"
          >
            <option value="all">All types</option>
            {filters.options.agentTypes.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <select
            value={filters.filters.language}
            onChange={(event) => filters.setFilter('language', event.target.value)}
            aria-label="Filter by language"
            className="rounded-xl border border-white/10 bg-black/50 px-4 py-2.5 text-sm text-white"
          >
            <option value="all">All languages</option>
            {filters.options.languages.map((language) => (
              <option key={language} value={language}>
                {language}
              </option>
            ))}
          </select>
          {(filters.activeCount > 0 || search.active) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                filters.reset();
                search.clear();
              }}
            >
              Clear
            </Button>
          )}
        </div>

        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((row) => (
              <div key={row} className="h-14 rounded-xl bg-white/5 animate-pulse" />
            ))}
          </div>
        ) : error ? (
          <GlassCard className="py-12 text-center">
            <div className="text-sm text-red-300">{error}</div>
            <Button size="sm" variant="ghost" className="mt-4" onClick={retry}>
              Retry
            </Button>
          </GlassCard>
        ) : sort.sorted.length === 0 ? (
          <GlassCard className="py-16 text-center">
            <h3 className="text-base font-medium text-white">
              {search.active || filters.activeCount > 0 ? 'No agents match' : 'No agents yet'}
            </h3>
            <p className="mx-auto mt-2 max-w-md text-sm text-white/60">
              {search.active || filters.activeCount > 0
                ? 'Try clearing the search box or resetting the filters.'
                : 'Create your first agent to configure prompts, voice, knowledge bases, and immutable release versions.'}
            </p>
            {!search.active && filters.activeCount === 0 && (
              <Button
                variant="primary"
                size="sm"
                className="mt-6"
                onClick={() => {
                  window.location.href = '/dashboard/agents/new';
                }}
              >
                Create your first agent
              </Button>
            )}
          </GlassCard>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-white/10">
            <table className="w-full text-left text-sm">
              <thead className="bg-white/[0.03] text-xs uppercase tracking-wide text-white/50">
                <tr>
                  {COLUMNS.map((column, index) => (
                    <th key={column.label || `actions-${index}`} className={`px-4 py-3 font-medium ${column.className ?? ''}`}>
                      {column.key ? (
                        <button
                          type="button"
                          onClick={() => sort.toggle(column.key as AgentSortKey)}
                          className="inline-flex items-center gap-1 hover:text-white"
                          aria-label={`Sort by ${column.label}`}
                        >
                          {column.label}
                          <span aria-hidden="true">
                            {sort.sort.key === column.key
                              ? sort.sort.direction === 'asc'
                                ? '↑'
                                : '↓'
                              : ''}
                          </span>
                        </button>
                      ) : (
                        column.label
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {sort.sorted.map((agent) => {
                  const status = String(agent.status ?? 'DRAFT').toUpperCase();
                  return (
                    <tr key={agent.id} className="hover:bg-white/[0.02]">
                      <td className="px-4 py-3">
                        <div className="font-medium text-white">{agent.name}</div>
                        {agent.description ? (
                          <div className="mt-0.5 line-clamp-1 text-xs text-white/45">
                            {agent.description}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${
                            STATUS_STYLES[status] ?? 'border-white/10 bg-white/5 text-white/60'
                          }`}
                        >
                          {status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-white/70">
                        {agent.version ? `v${agent.version}` : '—'}
                      </td>
                      <td className="px-4 py-3 text-white/70">{agent.language || '—'}</td>
                      <td className="px-4 py-3 text-xs text-white/50">
                        {formatTimestamp(agent.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3 text-xs">
                          <a
                            href={`/dashboard/agents/${encodeURIComponent(agent.id)}`}
                            className="text-blue-300 hover:text-blue-200"
                          >
                            Versions
                          </a>
                          <a
                            href={`/dashboard/agents/${encodeURIComponent(agent.id)}/builder`}
                            className="text-white/70 hover:text-white"
                          >
                            Builder
                          </a>
                          <a
                            href={`/dashboard/agents/${encodeURIComponent(agent.id)}/duplicate`}
                            className="text-white/70 hover:text-white"
                          >
                            Duplicate
                          </a>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <p className="mt-4 text-xs text-white/40">
          Showing {sort.sorted.length} of {counts.total} agents
          {search.active ? ` matching “${search.query}”` : ''}.
        </p>
      </div>
    </div>
  );
}

export default AgentListPage;
