import React, { useEffect, useState } from 'react';
import { useChatAgents } from '../../hooks/useChatAgents';

export function ChatAgentsPage() {
  const {
    agents,
    selectedAgent,
    setSelectedAgent,
    versions,
    validation,
    activeSession,
    messages,
    loading,
    error,
    conflictError,
    reload,
    create,
    saveDraft,
    validate,
    publish,
    rollback,
    archive,
    restore,
    startSession,
    sendMessage,
    closeSession,
  } = useChatAgents();

  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [firstMessage, setFirstMessage] = useState('');
  const [modelName, setModelName] = useState('gpt-4o-mini');
  const [temperature, setTemperature] = useState('0.2');
  const [changeSummary, setChangeSummary] = useState('');
  const [contactPhone, setContactPhone] = useState('+14155550199');
  const [contactName, setContactName] = useState('Alice Rahman');
  const [chatInput, setChatInput] = useState('');
  const [memoryKey, setMemoryKey] = useState('');
  const [memoryVal, setMemoryVal] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (selectedAgent) {
      const cfg = selectedAgent.draft_config || {};
      setSystemPrompt(String(cfg.system_prompt || ''));
      setFirstMessage(String(cfg.first_message || cfg.greeting || ''));
      setModelName(String(cfg.model || 'gpt-4o-mini'));
      setTemperature(String(cfg.temperature ?? 0.2));
    }
  }, [selectedAgent]);

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setNotice(null);
    try {
      await create(newName.trim(), newDescription.trim(), {
        system_prompt:
          'You are an enterprise omnichannel concierge. Answer clearly and concisely.',
        first_message: 'Hello! How can I help you today?',
        model: 'gpt-4o-mini',
        temperature: 0.2,
      });
      setNewName('');
      setNewDescription('');
      setNotice('Created durable chat agent.');
    } catch (err: any) {
      setNotice(err?.message || 'Failed to create chat agent');
    }
  };

  const handleSaveDraft = async () => {
    if (!selectedAgent) return;
    setNotice(null);
    try {
      await saveDraft(
        selectedAgent.id,
        {
          draft_config: {
            ...(selectedAgent.draft_config || {}),
            system_prompt: systemPrompt,
            first_message: firstMessage,
            model: modelName,
            temperature: parseFloat(temperature) || 0.2,
          },
        },
        selectedAgent.etag
      );
      setNotice('Saved durable draft with If-Match ETag verification.');
    } catch {
      // Error/conflict handled by hook
    }
  };

  const handlePublish = async () => {
    if (!selectedAgent) return;
    setNotice(null);
    try {
      const ver = await publish(
        selectedAgent.id,
        changeSummary.trim() || 'Published from Chat Agent Studio'
      );
      setChangeSummary('');
      setNotice(`Published immutable version v${ver.version}.`);
    } catch (err: any) {
      setNotice(err?.message || 'Publish failed');
    }
  };

  const handleSendChat = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSession || !chatInput.trim()) return;
    const memUpdates: Record<string, string> = {};
    if (memoryKey.trim() && memoryVal.trim()) {
      memUpdates[memoryKey.trim()] = memoryVal.trim();
    }
    try {
      await sendMessage(activeSession.id, chatInput.trim(), memUpdates);
      setChatInput('');
      setMemoryKey('');
      setMemoryVal('');
    } catch (err: any) {
      setNotice(err?.message || 'Failed to send message');
    }
  };

  return (
    <div className="min-h-screen bg-[#07070a] text-white p-6 md:p-10">
      <div className="mx-auto max-w-7xl space-y-8">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-white/10 pb-6">
          <div>
            <span className="text-xs uppercase tracking-widest text-blue-400 font-semibold">
              Omnichannel Studio
            </span>
            <h1 className="text-3xl font-bold tracking-tight mt-1">
              Durable Chat Agents & Sessions
            </h1>
            <p className="text-sm text-white/60 mt-1">
              PostgreSQL-backed ChatAgent, immutable ChatAgentVersion snapshots,
              ETag concurrency, and Contact Memory grounded chat sessions.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="/dashboard/contacts"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-4 py-2 text-xs font-medium text-white hover:bg-white/10"
            >
              Contacts & Memory
            </a>
            <button
              type="button"
              onClick={reload}
              className="rounded-xl border border-white/15 bg-white/[0.04] px-4 py-2 text-xs font-medium text-white hover:bg-white/10"
            >
              Refresh
            </button>
          </div>
        </div>

        {notice && (
          <div className="rounded-xl border border-blue-500/30 bg-blue-500/10 px-4 py-3 text-xs text-blue-200">
            {notice}
          </div>
        )}

        {conflictError && (
          <div
            role="alert"
            className="flex items-center justify-between rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-xs text-amber-200"
          >
            <span>{conflictError}</span>
            <button
              type="button"
              onClick={reload}
              className="rounded-lg bg-amber-500/20 border border-amber-500/40 px-3 py-1 font-medium text-amber-100 hover:bg-amber-500/30"
            >
              Reload Latest
            </button>
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs text-red-200">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left Column: Agent List + Create */}
          <div className="lg:col-span-4 space-y-6">
            <form
              onSubmit={handleCreateAgent}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 space-y-3"
            >
              <h2 className="text-sm font-semibold text-white">
                Create Chat Agent
              </h2>
              <input
                type="text"
                placeholder="Chat agent name (e.g. Billing Concierge)"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
              />
              <input
                type="text"
                placeholder="Description"
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
              />
              <button
                type="submit"
                className="w-full rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500"
              >
                Create Durable Chat Agent
              </button>
            </form>

            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
              <h2 className="text-sm font-semibold text-white">
                Chat Agents ({agents.length})
              </h2>
              {loading ? (
                <div className="h-24 animate-pulse rounded-xl bg-white/5" />
              ) : agents.length === 0 ? (
                <p className="text-xs text-white/40">
                  No chat agents created yet.
                </p>
              ) : (
                <div className="space-y-2">
                  {agents.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => setSelectedAgent(a)}
                      className={`w-full text-left rounded-xl border p-3 transition ${
                        selectedAgent?.id === a.id
                          ? 'border-blue-500/50 bg-blue-500/10'
                          : 'border-white/10 bg-black/40 hover:bg-white/[0.04]'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-white">
                          {a.name}
                        </span>
                        <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] uppercase text-white/80">
                          {a.status}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-white/50">
                        Draft v{a.draft_version} • Published{' '}
                        {a.published_version ? `v${a.published_version}` : '—'}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Draft Builder, Versions & Live Session Playground */}
          <div className="lg:col-span-8 space-y-6">
            {selectedAgent ? (
              <>
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h2 className="text-lg font-semibold text-white">
                        {selectedAgent.name}
                      </h2>
                      <p className="text-xs text-white/50">
                        ETag: <code>{selectedAgent.etag || '—'}</code> • Status:{' '}
                        {selectedAgent.status}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => validate(selectedAgent.id)}
                        className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white hover:bg-white/10"
                      >
                        Validate
                      </button>
                      <button
                        type="button"
                        onClick={handleSaveDraft}
                        className="rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500"
                      >
                        Save Draft
                      </button>
                      {selectedAgent.status !== 'archived' ? (
                        <button
                          type="button"
                          onClick={() => archive(selectedAgent.id)}
                          className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-200"
                        >
                          Archive
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => restore(selectedAgent.id)}
                          className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-200"
                        >
                          Restore
                        </button>
                      )}
                    </div>
                  </div>

                  {validation && (
                    <div
                      className={`rounded-xl border p-3 text-xs ${
                        validation.valid
                          ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                          : 'border-red-500/30 bg-red-500/10 text-red-200'
                      }`}
                    >
                      {validation.valid
                        ? 'Draft configuration is valid and ready to publish.'
                        : `Validation errors: ${validation.errors.join(', ')}`}
                    </div>
                  )}

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-white/60 mb-1">
                        Model
                      </label>
                      <input
                        type="text"
                        value={modelName}
                        onChange={(e) => setModelName(e.target.value)}
                        className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-white/60 mb-1">
                        Temperature
                      </label>
                      <input
                        type="number"
                        step="0.1"
                        value={temperature}
                        onChange={(e) => setTemperature(e.target.value)}
                        className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs text-white/60 mb-1">
                      First Message / Greeting
                    </label>
                    <input
                      type="text"
                      value={firstMessage}
                      onChange={(e) => setFirstMessage(e.target.value)}
                      className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                    />
                  </div>

                  <div>
                    <label className="block text-xs text-white/60 mb-1">
                      System Prompt
                    </label>
                    <textarea
                      rows={4}
                      value={systemPrompt}
                      onChange={(e) => setSystemPrompt(e.target.value)}
                      className="w-full rounded-xl border border-white/10 bg-black/50 p-3 text-xs text-white font-mono"
                    />
                  </div>

                  <div className="flex flex-col sm:flex-row items-center gap-3 pt-2 border-t border-white/10">
                    <input
                      type="text"
                      placeholder="Release notes / changelog summary"
                      value={changeSummary}
                      onChange={(e) => setChangeSummary(e.target.value)}
                      className="flex-1 w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                    />
                    <button
                      type="button"
                      onClick={handlePublish}
                      className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
                    >
                      Publish Immutable Version
                    </button>
                  </div>
                </div>

                {/* Immutable Version History */}
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-3">
                  <h3 className="text-sm font-semibold text-white">
                    Immutable Version History ({versions.length})
                  </h3>
                  {versions.length === 0 ? (
                    <p className="text-xs text-white/40">
                      No versions published yet.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {versions.map((v) => (
                        <div
                          key={v.id}
                          className="flex items-center justify-between rounded-xl border border-white/10 bg-black/40 p-3"
                        >
                          <div>
                            <div className="flex items-center gap-2 text-xs font-semibold text-white">
                              <span>v{v.version}</span>
                              <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px]">
                                {v.status}
                              </span>
                              {v.config_hash && (
                                <code className="text-[10px] text-white/40">
                                  #{v.config_hash}
                                </code>
                              )}
                            </div>
                            <p className="text-xs text-white/60 mt-0.5">
                              {v.change_summary || 'Published snapshot'}
                            </p>
                          </div>
                          {selectedAgent.published_version !== v.version && (
                            <button
                              type="button"
                              onClick={() =>
                                rollback(
                                  selectedAgent.id,
                                  v.version,
                                  `Rollback to v${v.version}`
                                )
                              }
                              className="rounded-lg border border-blue-500/30 bg-blue-500/15 px-3 py-1 text-xs text-blue-200 hover:bg-blue-500/25"
                            >
                              Rollback to v{v.version}
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Durable Chat Session & Messages Playground */}
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold text-white">
                        Durable Chat Session & Contact Memory Playground
                      </h3>
                      <p className="text-xs text-white/50">
                        Persists ChatSession and ordered ChatMessage rows with
                        automatic Contact & ContactMemoryEntry grounding.
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={contactPhone}
                        onChange={(e) => setContactPhone(e.target.value)}
                        placeholder="+14155550199"
                        className="rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white"
                      />
                      <input
                        type="text"
                        value={contactName}
                        onChange={(e) => setContactName(e.target.value)}
                        placeholder="Contact Name"
                        className="rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          startSession(selectedAgent.id, {
                            contact_phone: contactPhone,
                            contact_name: contactName,
                            channel: 'web',
                          })
                        }
                        className="rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500"
                      >
                        Start Session
                      </button>
                      {activeSession && activeSession.status === 'active' && (
                        <button
                          type="button"
                          onClick={() => closeSession(activeSession.id)}
                          className="rounded-xl border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white/80"
                        >
                          End Session
                        </button>
                      )}
                    </div>
                  </div>

                  {activeSession && (
                    <div className="space-y-3">
                      <div className="text-xs text-white/50">
                        Session <code>{activeSession.id}</code> • Status:{' '}
                        <span className="text-emerald-300">
                          {activeSession.status}
                        </span>{' '}
                        • Messages: {activeSession.message_count}
                      </div>

                      <div className="max-h-64 overflow-y-auto space-y-2 rounded-xl border border-white/10 bg-black/60 p-4">
                        {messages.map((m) => (
                          <div
                            key={m.id}
                            className={`rounded-xl p-3 text-xs ${
                              m.role === 'user'
                                ? 'bg-blue-600/20 border border-blue-500/30 text-blue-100 ml-8'
                                : 'bg-white/[0.05] border border-white/10 text-white mr-8'
                            }`}
                          >
                            <div className="font-semibold text-[10px] uppercase text-white/50 mb-1">
                              #{m.sequence} • {m.role}
                            </div>
                            <div>{m.content}</div>
                          </div>
                        ))}
                      </div>

                      <form onSubmit={handleSendChat} className="space-y-2">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          <input
                            type="text"
                            placeholder="Optional memory key to save (e.g. preferred_language)"
                            value={memoryKey}
                            onChange={(e) => setMemoryKey(e.target.value)}
                            className="rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white"
                          />
                          <input
                            type="text"
                            placeholder="Optional memory value (e.g. Bengali)"
                            value={memoryVal}
                            onChange={(e) => setMemoryVal(e.target.value)}
                            className="rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white"
                          />
                        </div>
                        <div className="flex gap-2">
                          <input
                            type="text"
                            placeholder="Send a message..."
                            value={chatInput}
                            onChange={(e) => setChatInput(e.target.value)}
                            className="flex-1 rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                          />
                          <button
                            type="submit"
                            className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
                          >
                            Send
                          </button>
                        </div>
                      </form>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-12 text-center text-sm text-white/40">
                Select or create a Chat Agent to configure its prompt, publish
                immutable versions, and run durable chat sessions.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default ChatAgentsPage;
