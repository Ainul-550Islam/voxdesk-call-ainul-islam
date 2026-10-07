/**
 * dashboard/src/pages/agents/AgentToolsPage.tsx
 *
 * Registered custom tools (functions) for one agent.
 *
 * Backed by `app/api/tool_registry_routes.py` through `useAgentTools`. The page
 * previously rendered a single placeholder line; it now performs real create,
 * update, enable/disable, and delete against the tool registry.
 *
 * Scope is stated on the page so nobody confuses these with the built-ins:
 * these are the *registered custom functions* for this agent. The built-in tool
 * contracts the runtime dispatches (`book_appointment`, `transfer_to_human`, …)
 * come from `app/agent/functions.py` and require an LLM provider that supports
 * tool calling; they are not editable here.
 */

import React, { useState } from 'react';
import { useAgentTools } from '../../hooks/useAgentTools';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const;

interface DraftTool {
  name: string;
  description: string;
  endpoint_url: string;
  http_method: (typeof HTTP_METHODS)[number];
  is_enabled: boolean;
}

const EMPTY_DRAFT: DraftTool = {
  name: '',
  description: '',
  endpoint_url: '',
  http_method: 'POST',
  is_enabled: true,
};

export function AgentToolsPage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');

  const { tools, loading, error, saving, enabledCount, disabledCount, reload, create, remove, setEnabled } =
    useAgentTools(id);

  const [draft, setDraft] = useState<DraftTool>(EMPTY_DRAFT);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    const name = draft.name.trim();
    if (!name) {
      setFormError('A tool name is required.');
      return;
    }
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      setFormError('Tool names must start with a letter or underscore and contain only letters, digits, and underscores.');
      return;
    }
    const created = await create({
      name,
      description: draft.description.trim(),
      schema: draft.endpoint_url
        ? { endpoint_url: draft.endpoint_url, http_method: draft.http_method }
        : {},
      auth_binding: {},
      is_enabled: draft.is_enabled,
    });
    if (created) setDraft(EMPTY_DRAFT);
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Agent tools</h1>
            <p className="text-xs text-white/50">
              Registered custom functions for this agent, stored per tenant
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={`/dashboard/agents/${encodeURIComponent(id)}/builder`}
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Back to builder
            </a>
            <Button variant="ghost" size="sm" onClick={reload}>
              Refresh
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <div className="grid gap-4 sm:grid-cols-3">
          <GlassCard>
            <div className="text-xs text-white/50">Registered tools</div>
            <div className="mt-2 text-2xl font-bold text-white">{tools.length}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Enabled</div>
            <div className="mt-2 text-2xl font-bold text-emerald-300">{enabledCount}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Disabled</div>
            <div className="mt-2 text-2xl font-bold text-white/60">{disabledCount}</div>
          </GlassCard>
        </div>

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Register a tool</h2>
          <form onSubmit={handleCreate} className="mt-4 grid gap-3 lg:grid-cols-2">
            <div>
              <label className="text-xs text-white/60" htmlFor="tool-name">
                Name (used by the model)
              </label>
              <input
                id="tool-name"
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="check_order_status"
                className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-white/60" htmlFor="tool-method">
                HTTP method
              </label>
              <select
                id="tool-method"
                value={draft.http_method}
                onChange={(event) =>
                  setDraft({ ...draft, http_method: event.target.value as DraftTool['http_method'] })
                }
                className="mt-1 w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white"
              >
                {HTTP_METHODS.map((method) => (
                  <option key={method} value={method}>
                    {method}
                  </option>
                ))}
              </select>
            </div>
            <div className="lg:col-span-2">
              <label className="text-xs text-white/60" htmlFor="tool-endpoint">
                Endpoint URL
              </label>
              <input
                id="tool-endpoint"
                value={draft.endpoint_url}
                onChange={(event) => setDraft({ ...draft, endpoint_url: event.target.value })}
                placeholder="https://api.example.com/orders/status"
                className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
              />
            </div>
            <div className="lg:col-span-2">
              <label className="text-xs text-white/60" htmlFor="tool-description">
                Description (what the model is told this tool does)
              </label>
              <textarea
                id="tool-description"
                value={draft.description}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                rows={3}
                className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
              />
            </div>
            <div className="flex items-center gap-2 lg:col-span-2">
              <input
                id="tool-enabled"
                type="checkbox"
                checked={draft.is_enabled}
                onChange={(event) => setDraft({ ...draft, is_enabled: event.target.checked })}
                className="h-4 w-4 rounded border-white/20 bg-black/50"
              />
              <label className="text-xs text-white/60" htmlFor="tool-enabled">
                Enabled immediately
              </label>
            </div>
            {formError && (
              <p className="text-xs text-red-300 lg:col-span-2" role="alert">
                {formError}
              </p>
            )}
            <div className="lg:col-span-2">
              <Button type="submit" variant="primary" size="sm" disabled={saving}>
                {saving ? 'Saving…' : 'Register tool'}
              </Button>
            </div>
          </form>
        </GlassCard>

        {error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Registered tools</h2>
          {loading ? (
            <div className="mt-4 space-y-2">
              {[0, 1].map((row) => (
                <div key={row} className="h-16 rounded-xl bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : tools.length === 0 ? (
            <p className="mt-4 text-sm text-white/60">
              No custom tools registered for this agent yet.
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-white/5">
              {tools.map((tool) => (
                <li key={tool.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-white">{tool.name}</span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs ${
                          tool.is_enabled
                            ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                            : 'border-white/10 bg-white/5 text-white/50'
                        }`}
                      >
                        {tool.is_enabled ? 'enabled' : 'disabled'}
                      </span>
                    </div>
                    {tool.description ? (
                      <p className="mt-1 text-xs text-white/60">{tool.description}</p>
                    ) : null}
                    {tool.schema?.endpoint_url ? (
                      <p className="mt-1 truncate font-mono text-xs text-white/40">
                        {String(tool.schema.http_method ?? 'POST')} {String(tool.schema.endpoint_url)}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={saving}
                      onClick={() => setEnabled(tool.id, !tool.is_enabled)}
                    >
                      {tool.is_enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={saving} onClick={() => remove(tool.id)}>
                      Delete
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 text-xs text-white/40">
            Note: the built-in tools the runtime dispatches (booking, escalation, knowledge lookup)
            are not listed here. They come from the runtime tool contract and require an LLM provider
            with tool-calling support.
          </p>
        </GlassCard>
      </div>
    </div>
  );
}

export default AgentToolsPage;
