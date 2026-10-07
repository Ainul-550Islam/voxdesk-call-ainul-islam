import React, { useState } from 'react';
import { useAgentBuilder } from '../../hooks/useAgentBuilder';
import { AgentBuilderHeader } from '../../components/agents/AgentBuilderHeader';
import { AgentBuilderSidebar } from '../../components/agents/AgentBuilderSidebar';
import { AgentPublishStatus } from '../../components/agents/AgentPublishStatus';
import { PromptEditor } from '../../components/agents/PromptEditor';
import { VoiceConfigPanel } from '../../components/agents/VoiceConfigPanel';
import { ModelConfigPanel } from '../../components/agents/ModelConfigPanel';
import { KnowledgeAttachmentPanel } from '../../components/agents/KnowledgeAttachmentPanel';
import { ToolsPanel } from '../../components/agents/ToolsPanel';
import { CallHandlingPanel } from '../../components/agents/CallHandlingPanel';
import { AgentTestPanel } from '../../components/agents/AgentTestPanel';
import { AgentVersionPanel } from '../../components/agents/AgentVersionPanel';
import { PublishAgentDialog } from '../../components/agents/PublishAgentDialog';
import { ConductorPanel } from '../../features/conductor/ConductorPanel';
import type { BuilderSection } from '../../types/agent-builder';

export function AgentBuilderPage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');

  const {
    config,
    localConfig,
    setLocalConfig,
    loading,
    error,
    saveState,
    lastSaved,
    isDirty,
    save,
    load,
    validation,
    doValidate,
    doPublish,
  } = useAgentBuilder(id);

  const [section, setSection] = useState<BuilderSection>('overview');
  const [showPublish, setShowPublish] = useState(false);

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="animate-pulse text-white/50">Loading durable agent builder...</div>
      </div>
    );
  }

  if (error && !config) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center p-4">
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-6 text-sm text-red-300 space-y-3">
          <div>{error}</div>
          <button
            type="button"
            onClick={load}
            className="rounded-xl border border-white/15 px-3 py-1.5 text-xs text-white"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white flex flex-col">
      <AgentBuilderHeader
        name={localConfig?.name || config?.name || 'Agent'}
        status={config?.status || 'DRAFT'}
        version={config?.version ?? null}
        etag={config?.etag || ''}
        saveState={saveState}
        lastSaved={lastSaved}
        onSave={save}
        onValidate={() => {
          doValidate();
        }}
        onTest={() => setSection('test')}
        onPublish={async () => {
          const v = await doValidate();
          if (v.valid) setShowPublish(true);
          else setSection('publish');
        }}
        onReload={load}
        onBack={() => {
          window.location.href = '/dashboard/agents';
        }}
      />

      <div className="flex flex-1">
        <AgentBuilderSidebar active={section} onSelect={setSection} />

        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8 py-8 space-y-6">
            {saveState === 'CONFLICT' && (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200 flex items-center justify-between gap-4">
                <span>
                  Draft conflict (HTTP 409 ETag mismatch): another session saved a newer
                  draft. Reload the latest configuration before saving.
                </span>
                <button
                  type="button"
                  onClick={load}
                  className="shrink-0 rounded-xl bg-amber-400 px-3 py-1.5 text-xs font-semibold text-black"
                >
                  Reload Latest
                </button>
              </div>
            )}

            {section === 'overview' && (
              <div className="space-y-6">
                <AgentPublishStatus
                  status={config?.status || 'DRAFT'}
                  activeVersion={config?.version ?? null}
                  validationStatus={
                    validation
                      ? validation.valid
                        ? 'valid'
                        : 'invalid'
                      : 'unvalidated'
                  }
                  saveState={saveState}
                  draftEtag={config?.etag || ''}
                  onValidate={() => {
                    doValidate();
                  }}
                  onPublish={async () => {
                    const v = await doValidate();
                    if (v.valid) setShowPublish(true);
                    else setSection('publish');
                  }}
                />

                <div className="space-y-4">
                  <div>
                    <label className="text-xs text-white/70">Agent Name</label>
                    <input
                      value={localConfig?.name || ''}
                      onChange={(e) =>
                        setLocalConfig({ ...localConfig!, name: e.target.value })
                      }
                      className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-sm text-white"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-white/70">Description</label>
                    <textarea
                      value={localConfig?.description || ''}
                      onChange={(e) =>
                        setLocalConfig({ ...localConfig!, description: e.target.value })
                      }
                      className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-sm text-white"
                    />
                  </div>
                </div>
              </div>
            )}

            {section === 'prompt' && (
              <PromptEditor
                value={localConfig?.system_prompt || ''}
                onChange={(v) =>
                  setLocalConfig({ ...localConfig!, system_prompt: v })
                }
                saveState={saveState}
              />
            )}

            {section === 'voice' && (
              <VoiceConfigPanel
                voiceId={localConfig?.voice_id}
                onSelect={(voiceId) =>
                  setLocalConfig({ ...localConfig!, voice_id: voiceId })
                }
              />
            )}

            {section === 'model' && (
              <ModelConfigPanel
                modelId={localConfig?.model_id}
                onSelect={(modelId) =>
                  setLocalConfig({ ...localConfig!, model_id: modelId })
                }
              />
            )}

            {section === 'knowledge' && <KnowledgeAttachmentPanel agentId={id} />}

            {section === 'tools' && <ToolsPanel agentId={id} />}

            {section === 'call_handling' && (
              <CallHandlingPanel
                config={localConfig?.config}
                onChange={(c) => setLocalConfig({ ...localConfig!, config: c })}
              />
            )}

            {section === 'versions' && <AgentVersionPanel agentId={id} />}

            {section === 'test' && <AgentTestPanel agentId={id} />}

            {section === 'publish' && (
              <div className="space-y-4">
                <h2 className="text-sm font-medium">Validate &amp; Publish</h2>
                <p className="text-xs text-white/50">
                  Draft → Validate → Confirmation → Durable AgentVersion snapshot +
                  Tenant runtime projection.
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={async () => {
                      const v = await doValidate();
                      if (v.valid) setShowPublish(true);
                    }}
                    className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-black"
                  >
                    Validate Configuration
                  </button>
                </div>
                {validation && (
                  <div className="space-y-2">
                    {validation.errors.map((e, i) => (
                      <div
                        key={i}
                        className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300"
                      >
                        {e.field}: {e.message}
                      </div>
                    ))}
                    {validation.warnings.map((w, i) => (
                      <div
                        key={i}
                        className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-3 text-xs text-amber-200"
                      >
                        {w.field}: {w.message}
                      </div>
                    ))}
                    {validation.valid && (
                      <div className="text-xs text-emerald-300">
                        ✓ Valid — ready to publish immutable version
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {id && (
              <div className="pt-4">
                <ConductorPanel
                  agentId={id}
                  agentKind="voice"
                  baseVersionNumber={config?.version ?? undefined}
                  originSurface="agent_builder"
                />
              </div>
            )}
          </div>
        </main>

        <aside className="hidden xl:block w-[380px] shrink-0 border-l border-white/10 bg-black/50 p-4">
          <div className="text-xs text-white/50">Live Configuration Summary</div>
          <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] p-4 text-xs text-white/60 space-y-1.5">
            <div>
              <span className="text-white/40">Agent:</span> {localConfig?.name}
            </div>
            <div>
              <span className="text-white/40">Status:</span> {config?.status}
            </div>
            <div>
              <span className="text-white/40">Save State:</span> {saveState}
            </div>
            <div>
              <span className="text-white/40">Active Version:</span> v
              {config?.version ?? 1}
            </div>
            <div>
              <span className="text-white/40">ETag:</span>{' '}
              <span className="font-mono">{config?.etag || '—'}</span>
            </div>
            <div>
              <span className="text-white/40">Unsaved Edits:</span>{' '}
              {isDirty ? 'Yes' : 'No'}
            </div>
          </div>
        </aside>
      </div>

      <div className="lg:hidden sticky bottom-0 border-t border-white/10 bg-black/80 backdrop-blur p-3 flex gap-2">
        <button
          type="button"
          onClick={save}
          className="flex-1 rounded-xl bg-white px-4 py-2.5 text-sm font-medium text-black"
        >
          Save
        </button>
        <button
          type="button"
          onClick={() => setSection('test')}
          className="flex-1 rounded-xl border border-white/20 px-4 py-2.5 text-sm text-white"
        >
          Test
        </button>
        <button
          type="button"
          onClick={() => setShowPublish(true)}
          className="flex-1 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white"
        >
          Publish
        </button>
      </div>

      <PublishAgentDialog
        open={showPublish}
        onClose={() => setShowPublish(false)}
        validation={validation}
        onPublish={async () => {
          const r = await doPublish();
          if (r.success) setShowPublish(false);
        }}
      />
    </div>
  );
}

export default AgentBuilderPage;
