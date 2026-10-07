/**
 * dashboard/src/pages/agents/AgentTestHistoryPage.tsx
 *
 * Test history for one agent.
 *
 * Two genuinely different things live here, and the page keeps them apart
 * rather than blurring them into one "history" list:
 *
 * 1. **Persisted simulation runs** — `GET /api/v1/testing/runs?agent_id=…`.
 *    These are durable rows: each one is pinned to an immutable agent version
 *    and keeps its transcript, events and usage metadata. This is the real
 *    history.
 *
 * 2. **A live browser test session** — `POST /api/v1/agents/{id}/test` then
 *    `POST /api/v1/agent-tests/{session_id}/events`. This exists only while the
 *    tab holds it; the runtime exposes no endpoint that lists past sessions, so
 *    the page never claims to. It is labelled "this browser session".
 *
 * The page previously rendered a single placeholder line.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { listTestRuns } from '../../api/simulations';
import type { TestRun } from '../../types/evaluation';
import { createTestSession, postTestEvent, getTestSession } from '../../api/agent-test';
import type { TestSession } from '../../types/agent-test';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

const RUN_STATUS_STYLES: Record<string, string> = {
  passed: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
  failed: 'border-red-500/30 bg-red-500/15 text-red-300',
  error: 'border-red-500/30 bg-red-500/15 text-red-300',
  running: 'border-blue-500/30 bg-blue-500/15 text-blue-300',
  pending: 'border-white/10 bg-white/5 text-white/50',
};

function formatTimestamp(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

export function AgentTestHistoryPage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');

  const [runs, setRuns] = useState<TestRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [session, setSession] = useState<TestSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [utterance, setUtterance] = useState('');

  const loadRuns = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listTestRuns({ agent_id: id, limit: 100 });
      setRuns(Array.isArray(rows) ? rows : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load test history');
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  async function startLiveSession() {
    setStarting(true);
    setSessionError(null);
    try {
      const created = await createTestSession(id);
      if (created.state === 'NOT_CONFIGURED') {
        setSessionError('This deployment has no configured voice provider, so a live test cannot start.');
        setSession(null);
        return;
      }
      setSession(created);
      const started = await postTestEvent(created.id, 'start');
      setSession(started);
    } catch (err) {
      setSessionError(err instanceof Error ? err.message : 'Failed to start a live test session');
    } finally {
      setStarting(false);
    }
  }

  async function sendUtterance(event: React.FormEvent) {
    event.preventDefault();
    const text = utterance.trim();
    if (!session || !text) return;
    setUtterance('');
    try {
      const updated = await postTestEvent(session.id, 'text', { text });
      setSession(updated);
    } catch (err) {
      setSessionError(err instanceof Error ? err.message : 'Failed to send the utterance');
    }
  }

  async function refreshSession() {
    if (!session) return;
    try {
      setSession(await getTestSession(session.id));
    } catch (err) {
      setSessionError(err instanceof Error ? err.message : 'Failed to refresh the test session');
    }
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Test history</h1>
            <p className="text-xs text-white/50">
              Persisted simulation runs, plus a live session for this browser
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={`/dashboard/agents/${encodeURIComponent(id)}/builder`}
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Back to builder
            </a>
            <a
              href="/dashboard/simulations"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Simulations
            </a>
            <Button variant="ghost" size="sm" onClick={loadRuns}>
              Refresh
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <div className="grid gap-4 sm:grid-cols-3">
          <GlassCard>
            <div className="text-xs text-white/50">Persisted runs</div>
            <div className="mt-2 text-2xl font-bold text-white">{runs.length}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Passed</div>
            <div className="mt-2 text-2xl font-bold text-emerald-300">
              {runs.filter((run) => String(run.status).toLowerCase() === 'passed').length}
            </div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Failed</div>
            <div className="mt-2 text-2xl font-bold text-red-300">
              {runs.filter((run) => ['failed', 'error'].includes(String(run.status).toLowerCase())).length}
            </div>
          </GlassCard>
        </div>

        <GlassCard>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-white">Live session (this browser)</h2>
              <p className="mt-1 text-xs text-white/50">
                The runtime exposes no endpoint that lists past browser sessions, so only the session
                started here is shown. Persisted runs are listed below.
              </p>
            </div>
            <div className="flex items-center gap-2">
              {session ? (
                <>
                  <Button size="sm" variant="ghost" onClick={refreshSession}>
                    Refresh
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => setSession(await postTestEvent(session.id, 'stop'))}
                  >
                    Stop
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="primary" disabled={starting} onClick={startLiveSession}>
                  {starting ? 'Starting…' : 'Start live test'}
                </Button>
              )}
            </div>
          </div>

          {sessionError && (
            <div className="mt-3 rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300">
              {sessionError}
            </div>
          )}

          {session && (
            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap items-center gap-3 text-xs text-white/50">
                <span>
                  session <span className="font-mono text-white/70">{session.id}</span>
                </span>
                <span>state {String(session.state)}</span>
              </div>
              <div className="max-h-72 space-y-2 overflow-y-auto rounded-xl border border-white/10 bg-black/40 p-3">
                {(session.transcript ?? []).length === 0 ? (
                  <p className="text-xs text-white/40">No turns yet.</p>
                ) : (
                  session.transcript.map((turn, index) => (
                    <div key={`${index}-${turn.role}`} className="text-xs">
                      <span
                        className={
                          turn.role === 'agent' ? 'font-medium text-blue-300' : 'font-medium text-white/70'
                        }
                      >
                        {turn.role === 'agent' ? 'Agent' : 'Caller'}:
                      </span>{' '}
                      <span className="text-white/70">{turn.content}</span>
                    </div>
                  ))
                )}
              </div>
              <form onSubmit={sendUtterance} className="flex gap-2">
                <input
                  value={utterance}
                  onChange={(event) => setUtterance(event.target.value)}
                  placeholder="Type what the caller says…"
                  aria-label="Caller utterance"
                  className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
                />
                <Button type="submit" size="sm" variant="primary" disabled={!utterance.trim()}>
                  Send
                </Button>
              </form>
            </div>
          )}
        </GlassCard>

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Persisted simulation runs</h2>
          {error && (
            <div className="mt-3 rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-300">
              {error}
            </div>
          )}
          {loading ? (
            <div className="mt-4 space-y-2">
              {[0, 1, 2].map((row) => (
                <div key={row} className="h-14 rounded-xl bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : runs.length === 0 ? (
            <p className="mt-4 text-sm text-white/60">
              No simulation runs have been recorded for this agent yet. Run one from the simulations
              screen and it will appear here, pinned to the agent version it tested.
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-white/5">
              {runs.map((run) => {
                const status = String(run.status ?? '').toLowerCase();
                return (
                  <li key={run.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-white/70">{run.id.slice(0, 12)}</span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs ${
                            RUN_STATUS_STYLES[status] ?? 'border-white/10 bg-white/5 text-white/60'
                          }`}
                        >
                          {status || 'unknown'}
                        </span>
                        {run.is_mock_provider ? (
                          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-200">
                            mock provider
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-white/45">
                        <span>mode {String(run.mode)}</span>
                        <span>v{run.agent_version_number}</span>
                        <span>
                          {run.provider} / {run.model}
                        </span>
                        <span>config {String(run.agent_config_hash).slice(0, 8)}</span>
                        <span>{formatTimestamp(run.created_at)}</span>
                      </div>
                    </div>
                    <a
                      href="/dashboard/simulations"
                      className="text-xs text-blue-300 hover:text-blue-200"
                    >
                      Open in simulations
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
        </GlassCard>
      </div>
    </div>
  );
}

export default AgentTestHistoryPage;
