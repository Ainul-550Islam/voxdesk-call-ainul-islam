/**
 * dashboard/src/pages/agents/AgentDuplicatePage.tsx
 *
 * Duplicate (clone) an agent.
 *
 * Backed by `POST /api/v1/agents/{agent_id}/clone`
 * (`app/api/agent_lifecycle_routes.py`), which copies the agent server-side and
 * returns the new agent's id. The page previously rendered a single placeholder
 * line; it now performs the real clone and links to the copy.
 *
 * The source agent's name and current version are read from
 * `GET /api/agents/{id}` so the confirmation screen shows what is actually
 * being copied, not a guess.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { apiClient } from '../../api/client';
import { getAgent } from '../../api/agents';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

export function AgentDuplicatePage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');

  const [sourceName, setSourceName] = useState('');
  const [sourceVersion, setSourceVersion] = useState<number | null>(null);
  const [newName, setNewName] = useState('');
  const [includeKnowledge, setIncludeKnowledge] = useState(true);
  const [includeTools, setIncludeTools] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [duplicating, setDuplicating] = useState(false);
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null);

  const load = useCallback(async () => {
    if (!id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const agent = await getAgent(id);
      setSourceName(agent.name);
      setSourceVersion(typeof agent.version === 'number' ? agent.version : null);
      setNewName(`${agent.name} (copy)`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the source agent');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleDuplicate(event: React.FormEvent) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) {
      setError('Give the copy a name.');
      return;
    }
    setDuplicating(true);
    setError(null);
    try {
      const res = await apiClient.post<Record<string, unknown>>(
        `/api/v1/agents/${encodeURIComponent(id)}/clone`,
        {
          new_name: name,
          include_knowledge_bases: includeKnowledge,
          include_tools: includeTools,
        },
      );
      setCreated({
        id: String(res?.new_agent_id ?? res?.id ?? ''),
        name: String(res?.name ?? name),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to duplicate the agent');
    } finally {
      setDuplicating(false);
    }
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Duplicate agent</h1>
            <p className="text-xs text-white/50">
              Creates a new draft agent from an existing configuration
            </p>
          </div>
          <a
            href="/dashboard/agents"
            className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
          >
            All agents
          </a>
        </div>
      </div>

      <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        {error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        {created ? (
          <GlassCard className="text-center">
            <h2 className="text-base font-medium text-white">Copy created</h2>
            <p className="mt-2 text-sm text-white/60">
              “{created.name}” is a separate draft agent. Its version history starts fresh; the
              original is untouched.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  window.location.href = `/dashboard/agents/${encodeURIComponent(created.id)}/builder`;
                }}
              >
                Open the copy in the builder
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  window.location.href = '/dashboard/agents';
                }}
              >
                Back to all agents
              </Button>
            </div>
          </GlassCard>
        ) : (
          <GlassCard>
            {loading ? (
              <div className="space-y-3">
                <div className="h-4 w-40 rounded bg-white/5 animate-pulse" />
                <div className="h-10 rounded-xl bg-white/5 animate-pulse" />
              </div>
            ) : (
              <form onSubmit={handleDuplicate} className="space-y-4">
                <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
                  <div className="text-xs text-white/50">Copying</div>
                  <div className="mt-1 text-sm text-white">{sourceName || 'this agent'}</div>
                  <div className="mt-1 text-xs text-white/45">
                    {sourceVersion ? `from version v${sourceVersion}` : 'from the current draft'}
                  </div>
                </div>

                <div>
                  <label className="text-xs text-white/60" htmlFor="duplicate-name">
                    Name for the copy
                  </label>
                  <input
                    id="duplicate-name"
                    value={newName}
                    onChange={(event) => setNewName(event.target.value)}
                    className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white focus:border-blue-500/50 focus:outline-none"
                  />
                </div>

                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-xs text-white/70">
                    <input
                      type="checkbox"
                      checked={includeKnowledge}
                      onChange={(event) => setIncludeKnowledge(event.target.checked)}
                      className="h-4 w-4 rounded border-white/20 bg-black/50"
                    />
                    Include knowledge-base attachments
                  </label>
                  <label className="flex items-center gap-2 text-xs text-white/70">
                    <input
                      type="checkbox"
                      checked={includeTools}
                      onChange={(event) => setIncludeTools(event.target.checked)}
                      className="h-4 w-4 rounded border-white/20 bg-black/50"
                    />
                    Include registered tools
                  </label>
                </div>

                <Button type="submit" variant="primary" size="sm" disabled={duplicating || !newName.trim()}>
                  {duplicating ? 'Duplicating…' : 'Duplicate agent'}
                </Button>
              </form>
            )}
          </GlassCard>
        )}
      </div>
    </div>
  );
}

export default AgentDuplicatePage;
