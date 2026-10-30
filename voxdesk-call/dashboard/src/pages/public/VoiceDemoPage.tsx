import React, { useState } from 'react';
import {
  createVoiceDemoSession,
  endVoiceDemoSession,
  postVoiceDemoEvent,
  postVoiceDemoTurn,
} from '../../api/voice-demo';
import type {
  VoiceDemoSession,
  VoiceDemoTranscriptTurn,
  VoiceState,
} from '../../types/voice';

export function VoiceDemoPage() {
  const [session, setSession] = useState<VoiceDemoSession | null>(null);
  const [state, setState] = useState<VoiceState>('IDLE');
  const [transcript, setTranscript] = useState<VoiceDemoTranscriptTurn[]>([]);
  const [utterance, setUtterance] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStartSession = async () => {
    setLoading(true);
    setError(null);
    try {
      const created = await createVoiceDemoSession();
      setSession(created);
      setTranscript(created.transcript || []);
      const started = await postVoiceDemoEvent(
        created.session_id,
        { event: 'start', timestamp: new Date().toISOString(), data: {} },
        created.session_token,
      );
      setState(started.new_state);
      if (started.transcript) {
        setTranscript(started.transcript);
      }
    } catch (e: unknown) {
      setState('ERROR');
      setError(e instanceof Error ? e.message : 'Unable to start public voice demo session.');
    } finally {
      setLoading(false);
    }
  };

  const handleSendTurn = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!session || !session.session_token || !utterance.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await postVoiceDemoTurn(
        session.session_id,
        utterance.trim(),
        session.session_token,
      );
      setUtterance('');
      setState(res.new_state);
      if (res.transcript) {
        setTranscript(res.transcript);
      }
    } catch (e: unknown) {
      setState('ERROR');
      setError(e instanceof Error ? e.message : 'Demo turn failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleEndSession = async () => {
    if (!session || !session.session_token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await endVoiceDemoSession(session.session_id, session.session_token);
      setState(res.new_state);
      setSession(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not close demo session.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <div className="mx-auto max-w-3xl space-y-6">
        <header className="space-y-2">
          <span className="inline-block rounded-full bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">
            Live Public Voice Demo
          </span>
          <h1 className="text-3xl font-semibold tracking-tight text-white">
            Interactive Voice Agent Demo
          </h1>
          <p className="text-sm text-slate-400">
            Sessions are persisted in PostgreSQL and authenticated with short-lived HMAC session tokens.
          </p>
        </header>

        {error && (
          <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
            {error}
          </div>
        )}

        <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-wider text-slate-400">Session State</p>
              <p className="text-lg font-medium text-white" data-testid="voice-demo-state">
                {state}
              </p>
              {session?.agent_name && (
                <p className="text-xs text-slate-400">Connected agent: {session.agent_name}</p>
              )}
            </div>
            <div className="flex gap-3">
              {!session ? (
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void handleStartSession()}
                  className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  Start Demo Session
                </button>
              ) : (
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void handleEndSession()}
                  className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-sm font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
                >
                  End Session
                </button>
              )}
            </div>
          </div>

          {session && (
            <form onSubmit={handleSendTurn} className="flex gap-3">
              <input
                type="text"
                aria-label="Say something to the demo agent"
                value={utterance}
                onChange={(e) => setUtterance(e.target.value)}
                placeholder="Ask a question…"
                className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2 text-sm text-white"
              />
              <button
                type="submit"
                disabled={loading || !utterance.trim()}
                className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                Send Turn
              </button>
            </form>
          )}

          <div className="space-y-2 border-t border-slate-800 pt-4">
            <h2 className="text-xs font-medium uppercase tracking-wider text-slate-400">
              Transcript
            </h2>
            {transcript.length === 0 ? (
              <p className="text-xs text-slate-500">Start a demo session to converse with the configured agent.</p>
            ) : (
              <ul className="space-y-2" data-testid="voice-demo-transcript">
                {transcript.map((turn, idx) => (
                  <li
                    key={`${turn.timestamp}-${idx}`}
                    className="rounded-xl border border-slate-800 bg-slate-950/70 px-3.5 py-2.5 text-sm"
                  >
                    <span className="mr-2 text-xs font-semibold uppercase text-blue-300">
                      {turn.role}:
                    </span>
                    <span className="text-slate-100">{turn.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

export default VoiceDemoPage;
