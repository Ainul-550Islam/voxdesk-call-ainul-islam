/**
 * dashboard/src/pages/agents/AgentVoicePage.tsx
 *
 * Voice configuration for one agent.
 *
 * Backed by `GET /api/agents/voices` through `useAgentVoiceCatalog`, and saved
 * through `PATCH /api/v1/agents/{agent_id}/builder` with the draft ETag
 * (`If-Match`), so two people editing the same agent cannot silently overwrite
 * each other.
 *
 * The page previously rendered a single placeholder line. It now shows:
 * - the providers this deployment can actually use, and, for every provider it
 *   cannot, the server's own reason;
 * - only the voice IDs this deployment has configured, with an explicit note
 *   that a provider's full library is not fetched here;
 * - an explicit "configuration is not connectivity" banner, because
 *   `configured` only means a key exists.
 */

import React, { useEffect, useState } from 'react';
import { useAgentVoiceCatalog } from '../../hooks/useAgentVoiceCatalog';
import { useAgentSave } from '../../hooks/useAgentSave';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

export function AgentVoicePage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');

  const catalog = useAgentVoiceCatalog();
  const save = useAgentSave(id);

  const [provider, setProvider] = useState('');
  const [voiceId, setVoiceId] = useState('');
  const [speed, setSpeed] = useState(1.0);
  const [stability, setStability] = useState(0.75);
  const [similarityBoost, setSimilarityBoost] = useState(0.75);
  const [initialised, setInitialised] = useState(false);

  // Seed the form from the agent's saved draft the first time it arrives, and
  // never clobber in-progress edits afterwards.
  useEffect(() => {
    if (initialised || !save.draft?.voice) return;
    const voice = save.draft.voice as Record<string, unknown>;
    setProvider(String(voice.provider ?? ''));
    setVoiceId(String(voice.voice_id ?? ''));
    setSpeed(Number(voice.speed ?? 1.0));
    setStability(Number(voice.stability ?? 0.75));
    setSimilarityBoost(Number(voice.similarity_boost ?? 0.75));
    setInitialised(true);
  }, [save.draft, initialised]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    save.update({
      voice: {
        provider,
        voice_id: voiceId,
        speed,
        pitch: 1.0,
        stability,
        similarity_boost: similarityBoost,
      },
    });
    await save.save();
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Voice configuration</h1>
            <p className="text-xs text-white/50">
              Text-to-speech provider, voice, and prosody settings
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={`/dashboard/agents/${encodeURIComponent(id)}/builder`}
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Back to builder
            </a>
            <Button variant="ghost" size="sm" onClick={catalog.reload}>
              Refresh catalog
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <div className="rounded-xl border border-blue-500/20 bg-blue-500/10 p-4 text-xs text-blue-200">
          <strong className="font-semibold">Configuration is not connectivity.</strong>{' '}
          “Configured” means an API key exists in this deployment's settings. It does not mean the
          provider was reached or the key accepted — no network call is made while loading this page.
        </div>

        {catalog.error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {catalog.error}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-3">
          <GlassCard className="lg:col-span-2">
            <h2 className="text-sm font-semibold text-white">Voice settings</h2>
            {save.state === 'conflict' && (
              <div className="mt-3 rounded-xl border border-amber-500/25 bg-amber-500/10 p-3 text-xs text-amber-200">
                {save.error}{' '}
                <button
                  type="button"
                  className="underline hover:text-amber-100"
                  onClick={save.reloadFromServer}
                >
                  Reload the server's version
                </button>
              </div>
            )}
            <form onSubmit={handleSubmit} className="mt-4 space-y-4">
              <div>
                <label className="text-xs text-white/60" htmlFor="voice-provider">
                  Provider
                </label>
                <select
                  id="voice-provider"
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                  className="mt-1 w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white"
                >
                  <option value="">Select a provider…</option>
                  {catalog.selectableProviders.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.label} — ready
                    </option>
                  ))}
                  {catalog.unavailableProviders.map((entry) => (
                    <option key={entry.id} value={entry.id} disabled>
                      {entry.label} — {entry.reason}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs text-white/60" htmlFor="voice-id">
                  Voice ID
                </label>
                <input
                  id="voice-id"
                  value={voiceId}
                  onChange={(event) => setVoiceId(event.target.value)}
                  placeholder="Voice identifier"
                  className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 font-mono text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
                />
                <p className="mt-1 text-xs text-white/40">
                  {catalog.voiceLibraryNote}
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <div>
                  <label className="text-xs text-white/60" htmlFor="voice-speed">
                    Speed: {speed.toFixed(2)}
                  </label>
                  <input
                    id="voice-speed"
                    type="range"
                    min={0.5}
                    max={2}
                    step={0.05}
                    value={speed}
                    onChange={(event) => setSpeed(Number(event.target.value))}
                    className="mt-2 w-full"
                  />
                </div>
                <div>
                  <label className="text-xs text-white/60" htmlFor="voice-stability">
                    Stability: {stability.toFixed(2)}
                  </label>
                  <input
                    id="voice-stability"
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={stability}
                    onChange={(event) => setStability(Number(event.target.value))}
                    className="mt-2 w-full"
                  />
                </div>
                <div>
                  <label className="text-xs text-white/60" htmlFor="voice-similarity">
                    Similarity boost: {similarityBoost.toFixed(2)}
                  </label>
                  <input
                    id="voice-similarity"
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={similarityBoost}
                    onChange={(event) => setSimilarityBoost(Number(event.target.value))}
                    className="mt-2 w-full"
                  />
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button type="submit" variant="primary" size="sm" disabled={save.state === 'saving'}>
                  {save.state === 'saving' ? 'Saving…' : 'Save voice settings'}
                </Button>
                {save.lastSavedAt ? (
                  <span className="text-xs text-emerald-300">
                    Saved {new Date(save.lastSavedAt).toLocaleTimeString()}
                  </span>
                ) : null}
                {save.error && save.state !== 'conflict' ? (
                  <span className="text-xs text-red-300">{save.error}</span>
                ) : null}
              </div>
            </form>
          </GlassCard>

          <div className="space-y-6">
            <GlassCard>
              <h2 className="text-sm font-semibold text-white">Available providers</h2>
              {catalog.loading ? (
                <div className="mt-3 space-y-2">
                  {[0, 1, 2].map((row) => (
                    <div key={row} className="h-10 rounded-lg bg-white/5 animate-pulse" />
                  ))}
                </div>
              ) : (
                <ul className="mt-3 space-y-2">
                  {catalog.providers.map((entry) => (
                    <li
                      key={entry.id}
                      className="rounded-xl border border-white/10 bg-white/[0.02] p-3"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm text-white">{entry.label}</span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs ${
                            entry.selectable
                              ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                              : 'border-white/10 bg-white/5 text-white/50'
                          }`}
                        >
                          {entry.selectable ? 'ready' : 'unavailable'}
                        </span>
                      </div>
                      {entry.reason ? (
                        <p className="mt-1 text-xs text-white/45">{entry.reason}</p>
                      ) : (
                        <p className="mt-1 text-xs text-white/45">
                          {entry.distribution} {entry.sdk_version ? `v${entry.sdk_version}` : ''}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </GlassCard>

            <GlassCard>
              <h2 className="text-sm font-semibold text-white">Configured voices</h2>
              {catalog.configuredVoices.length === 0 ? (
                <p className="mt-2 text-xs text-white/50">
                  No voices are configured in this deployment yet.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {catalog.configuredVoices.map((voice) => (
                    <li key={`${voice.provider}-${voice.voice_id}`}>
                      <button
                        type="button"
                        onClick={() => {
                          setProvider(voice.provider);
                          setVoiceId(voice.voice_id);
                        }}
                        className="w-full rounded-xl border border-white/10 bg-white/[0.02] p-3 text-left hover:bg-white/[0.05]"
                      >
                        <div className="font-mono text-xs text-white">{voice.voice_id}</div>
                        <div className="mt-0.5 text-xs text-white/45">
                          {voice.provider} · {voice.source}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </GlassCard>
          </div>
        </div>
      </div>
    </div>
  );
}

export default AgentVoicePage;
