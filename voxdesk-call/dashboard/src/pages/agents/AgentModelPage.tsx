/**
 * dashboard/src/pages/agents/AgentModelPage.tsx
 *
 * LLM configuration for one agent.
 *
 * Backed by `GET /api/agents/models` through `useAgentModelCatalog`, and saved
 * through `PATCH /api/v1/agents/{agent_id}/builder` with the draft ETag.
 *
 * The page previously rendered a single placeholder line. It now shows the
 * runtime's real model presets (`llm_factory.PRESETS`) — the exact
 * provider/model pairs the voice runtime constructs — rather than a guessed
 * dropdown of model names.
 *
 * When this deployment has no selectable preset, `defaultProvider` /
 * `defaultPreset` are null and the page says so instead of pre-selecting a
 * model that would fail at call time.
 */

import React, { useEffect, useState } from 'react';
import { useAgentModelCatalog } from '../../hooks/useAgentModelCatalog';
import { useAgentSave } from '../../hooks/useAgentSave';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

const RESPONSE_STYLES = ['concise', 'balanced', 'detailed', 'conversational'] as const;
type ResponseStyle = (typeof RESPONSE_STYLES)[number];

export function AgentModelPage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');

  const catalog = useAgentModelCatalog();
  const save = useAgentSave(id);

  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [temperature, setTemperature] = useState(0.3);
  const [maxTokens, setMaxTokens] = useState(512);
  const [responseStyle, setResponseStyle] = useState<ResponseStyle>('conversational');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [contextTurns, setContextTurns] = useState(20);
  const [initialised, setInitialised] = useState(false);

  useEffect(() => {
    if (initialised || !save.draft?.model) return;
    const config = save.draft.model as Record<string, unknown>;
    setProvider(String(config.provider ?? ''));
    setModel(String(config.model_name ?? ''));
    setTemperature(Number(config.temperature ?? 0.3));
    setMaxTokens(Number(config.max_tokens ?? 512));
    setResponseStyle((String(config.response_style ?? 'conversational') as ResponseStyle) ?? 'conversational');
    setSystemPrompt(String(config.system_prompt ?? ''));
    setContextTurns(Number(config.context_window_turns ?? 20));
    setInitialised(true);
  }, [save.draft, initialised]);

  function applyPreset(preset: string) {
    const match = catalog.presets.find((entry) => entry.preset === preset);
    if (!match) return;
    setProvider(match.provider);
    setModel(match.model);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    save.update({
      model: {
        provider,
        model_name: model,
        temperature,
        max_tokens: maxTokens,
        system_prompt: systemPrompt,
        context_window_turns: contextTurns,
        response_style: responseStyle,
      },
    });
    await save.save();
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Model configuration</h1>
            <p className="text-xs text-white/50">
              LLM provider, model, sampling, and conversation context
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
          <strong className="font-semibold">Configuration is not connectivity.</strong> Model names
          come from the runtime's own presets, so this list can never offer a model the voice
          pipeline does not build. “Configured” means an API key exists — not that the provider was
          reached.
        </div>

        {catalog.error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {catalog.error}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-3">
          <GlassCard className="lg:col-span-2">
            <h2 className="text-sm font-semibold text-white">Model settings</h2>
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
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="text-xs text-white/60" htmlFor="model-provider">
                    Provider
                  </label>
                  <select
                    id="model-provider"
                    value={provider}
                    onChange={(event) => setProvider(event.target.value)}
                    className="mt-1 w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white"
                  >
                    <option value="">Select a provider…</option>
                    {catalog.providers.map((entry) => (
                      <option key={entry.id} value={entry.id} disabled={!entry.selectable}>
                        {entry.label}
                        {entry.selectable ? '' : ` — ${entry.reason}`}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-white/60" htmlFor="model-name">
                    Model
                  </label>
                  <input
                    id="model-name"
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 font-mono text-sm text-white focus:border-blue-500/50 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <span className="text-xs text-white/60">Runtime presets</span>
                <div className="mt-2 flex flex-wrap gap-2">
                  {catalog.presets.map((preset) => (
                    <button
                      key={preset.preset}
                      type="button"
                      disabled={!preset.selectable}
                      onClick={() => applyPreset(preset.preset)}
                      title={`${preset.provider} / ${preset.model} — ~${preset.est_latency_ms}ms first token`}
                      className={`rounded-xl border px-3 py-1.5 text-xs ${
                        preset.selectable
                          ? 'border-white/15 bg-white/[0.04] text-white/80 hover:bg-white/10'
                          : 'border-white/5 bg-white/[0.01] text-white/30'
                      }`}
                    >
                      {preset.preset} · {preset.model}
                      {preset.selectable ? '' : ' (unavailable)'}
                    </button>
                  ))}
                </div>
                {catalog.defaultPreset === null && !catalog.loading ? (
                  <p className="mt-2 text-xs text-amber-300">
                    No model provider is configured in this deployment, so no preset can be selected.
                    Add an LLM API key to enable one.
                  </p>
                ) : null}
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="text-xs text-white/60" htmlFor="model-temperature">
                    Temperature: {temperature.toFixed(2)}
                  </label>
                  <input
                    id="model-temperature"
                    type="range"
                    min={0}
                    max={2}
                    step={0.05}
                    value={temperature}
                    onChange={(event) => setTemperature(Number(event.target.value))}
                    className="mt-2 w-full"
                  />
                </div>
                <div>
                  <label className="text-xs text-white/60" htmlFor="model-max-tokens">
                    Max tokens
                  </label>
                  <input
                    id="model-max-tokens"
                    type="number"
                    min={32}
                    max={4096}
                    value={maxTokens}
                    onChange={(event) => setMaxTokens(Number(event.target.value))}
                    className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white focus:border-blue-500/50 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="text-xs text-white/60" htmlFor="model-style">
                    Response style
                  </label>
                  <select
                    id="model-style"
                    value={responseStyle}
                    onChange={(event) => setResponseStyle(event.target.value as ResponseStyle)}
                    className="mt-1 w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white"
                  >
                    {RESPONSE_STYLES.map((style) => (
                      <option key={style} value={style}>
                        {style}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-white/60" htmlFor="model-context">
                    Context window (turns)
                  </label>
                  <input
                    id="model-context"
                    type="number"
                    min={1}
                    max={100}
                    value={contextTurns}
                    onChange={(event) => setContextTurns(Number(event.target.value))}
                    className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white focus:border-blue-500/50 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs text-white/60" htmlFor="model-prompt">
                  System prompt
                </label>
                <textarea
                  id="model-prompt"
                  value={systemPrompt}
                  onChange={(event) => setSystemPrompt(event.target.value)}
                  rows={8}
                  className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 font-mono text-sm text-white focus:border-blue-500/50 focus:outline-none"
                />
              </div>

              <div className="flex items-center gap-3">
                <Button type="submit" variant="primary" size="sm" disabled={save.state === 'saving'}>
                  {save.state === 'saving' ? 'Saving…' : 'Save model settings'}
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
              <h2 className="text-sm font-semibold text-white">Configured models</h2>
              <dl className="mt-3 space-y-2 text-xs">
                <div className="flex justify-between gap-2">
                  <dt className="text-white/50">STT model</dt>
                  <dd className="font-mono text-white/80">{catalog.configuredSttModel || '—'}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-white/50">TTS model</dt>
                  <dd className="font-mono text-white/80">{catalog.configuredTtsModel || '—'}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-white/50">Default preset</dt>
                  <dd className="font-mono text-white/80">{catalog.defaultPreset ?? 'none'}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-white/50">Default provider</dt>
                  <dd className="font-mono text-white/80">{catalog.defaultProvider ?? 'none'}</dd>
                </div>
              </dl>
            </GlassCard>

            <GlassCard>
              <h2 className="text-sm font-semibold text-white">Providers</h2>
              {catalog.loading ? (
                <div className="mt-3 space-y-2">
                  {[0, 1, 2].map((row) => (
                    <div key={row} className="h-10 rounded-lg bg-white/5 animate-pulse" />
                  ))}
                </div>
              ) : (
                <ul className="mt-3 space-y-2">
                  {catalog.providers.map((entry) => (
                    <li key={entry.id} className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
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
          </div>
        </div>
      </div>
    </div>
  );
}

export default AgentModelPage;
