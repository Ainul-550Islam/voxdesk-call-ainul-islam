import React from 'react';
import { useAgents } from '../../hooks/useAgents';
import { AgentCard } from '../../components/agents/AgentCard';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

export function AgentsPage() {
  const {
    agents,
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
  } = useAgents();

  const handleOpenBuilder = (id: string) => {
    window.location.href = `/dashboard/agents/${encodeURIComponent(id)}/builder`;
  };

  const handleOpenDetail = (id: string) => {
    window.location.href = `/dashboard/agents/${encodeURIComponent(id)}`;
  };

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-white">Voice &amp; Chat Agents</h1>
            <p className="text-xs text-white/50">
              Durable tenant-scoped agents with immutable version history
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href="/dashboard/chat-agents"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Chat Agents
            </a>
            <a
              href="/dashboard/contacts"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Contacts &amp; Memory
            </a>
            <a
              href="/dashboard/playground"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Playground
            </a>
            <a
              href="/dashboard/simulations"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Simulations
            </a>
            <a
              href="/dashboard/qa-scorecards"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              QA Scorecards
            </a>
            <a
              href="/studio/conductor"
              className="rounded-xl border border-blue-500/40 bg-blue-500/15 px-3 py-1.5 text-xs font-semibold text-blue-200 hover:bg-blue-500/25"
            >
              Conductor AI
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

      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex flex-col sm:flex-row gap-4 mb-8">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search agents by name..."
            className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] backdrop-blur px-4 py-2.5 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            aria-label="Search agents"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter agents by status"
            className="rounded-xl border border-white/10 bg-black/50 px-4 py-2.5 text-sm text-white"
          >
            <option value="all">All Status</option>
            <option value="DRAFT">Draft</option>
            <option value="PUBLISHED">Published</option>
            <option value="ARCHIVED">Archived</option>
          </select>
          <Button variant="ghost" size="sm" onClick={retry}>
            Refresh
          </Button>
        </div>

        <div className="grid gap-4 sm:grid-cols-3 mb-8">
          <GlassCard>
            <div className="text-xs text-white/50">Total Agents</div>
            <div className="mt-2 text-2xl font-bold text-white">{total}</div>
            <div className="text-xs text-white/35">PostgreSQL tenant-scoped</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Published</div>
            <div className="mt-2 text-2xl font-bold text-emerald-300">{published}</div>
            <div className="text-xs text-white/35">Live runtime active</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Drafts</div>
            <div className="mt-2 text-2xl font-bold text-amber-300">{draft}</div>
            <div className="text-xs text-white/35">ETag concurrency protected</div>
          </GlassCard>
        </div>

        {loading ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-48 rounded-2xl bg-white/5 animate-pulse" />
            ))}
          </div>
        ) : error ? (
          <GlassCard className="text-center py-16">
            <div className="text-sm text-red-300">{error}</div>
            <Button size="sm" variant="ghost" className="mt-4" onClick={retry}>
              Retry
            </Button>
          </GlassCard>
        ) : agents.length === 0 ? (
          <GlassCard className="text-center py-16">
            <div className="mx-auto max-w-md">
              <div className="h-12 w-12 rounded-full bg-white/10 flex items-center justify-center mx-auto text-lg">
                🤖
              </div>
              <h3 className="mt-4 text-base font-medium text-white">No agents yet</h3>
              <p className="mt-2 text-sm text-white/60">
                Create your first voice agent to configure prompts, voice synthesis,
                knowledge bases, and immutable release versions.
              </p>
              <Button
                variant="primary"
                size="sm"
                className="mt-6"
                onClick={() => {
                  window.location.href = '/dashboard/agents/new';
                }}
              >
                Create your first voice agent
              </Button>
            </div>
          </GlassCard>
        ) : (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <div key={agent.id} className="space-y-2">
                <AgentCard agent={agent} onOpen={handleOpenBuilder} />
                <div className="flex items-center justify-between px-2 text-xs text-white/50">
                  <span>
                    {agent.version ? `v${agent.version}` : 'Draft'} • {agent.status}
                  </span>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => handleOpenDetail(agent.id)}
                      className="text-blue-300 hover:text-blue-200"
                    >
                      Details &amp; Versions
                    </button>
                    <button
                      type="button"
                      onClick={() => handleOpenBuilder(agent.id)}
                      className="text-white/70 hover:text-white"
                    >
                      Builder →
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default AgentsPage;
