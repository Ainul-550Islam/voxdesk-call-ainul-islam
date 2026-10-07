/**
 * dashboard/src/pages/agents/AgentKnowledgePage.tsx
 *
 * Knowledge-base manager for the Agent Studio.
 *
 * Backed by `app/api/knowledge_routes.py` through `useAgentKnowledge`. This
 * page previously rendered a single placeholder line; it now performs real
 * upload, listing, retrieval preview, reindex, restore, and archive.
 *
 * Honest-behaviour notes that shape the UI:
 * - Upload returns 202 and indexes in the background, so newly uploaded
 *   documents are polled until they reach a terminal status. Until then they
 *   show as "indexing", never as "ready".
 * - A document that failed ingestion shows the server's own `error_message`
 *   rather than a generic "failed".
 * - Retrieval preview (`POST /api/knowledge/search`) is labelled as a tuning
 *   aid with real similarity scores — it is not presented as "what the agent
 *   will say".
 */

import React, { useMemo, useRef, useState } from 'react';
import { useAgentKnowledge } from '../../hooks/useAgentKnowledge';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';

const STATUS_STYLES: Record<string, string> = {
  ready: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
  failed: 'border-red-500/30 bg-red-500/15 text-red-300',
  uploaded: 'border-blue-500/30 bg-blue-500/15 text-blue-300',
  processing: 'border-blue-500/30 bg-blue-500/15 text-blue-300',
  indexing: 'border-blue-500/30 bg-blue-500/15 text-blue-300',
  archived: 'border-white/10 bg-white/5 text-white/50',
};

function statusClass(status: string): string {
  return STATUS_STYLES[status.toLowerCase()] ?? 'border-white/10 bg-white/5 text-white/60';
}

function formatBytes(size: number): string {
  if (!size || size < 0) return '—';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function AgentKnowledgePage() {
  const {
    documents,
    total,
    stats,
    loading,
    error,
    uploading,
    pendingIds,
    searchResults,
    searching,
    reload,
    upload,
    remove,
    reindex,
    restore,
    ingestUrl,
    search,
    clearSearch,
  } = useAgentKnowledge();

  const fileInput = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState('');
  const [url, setUrl] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);

  const readyCount = useMemo(
    () => documents.filter((doc) => String(doc.status).toLowerCase() === 'ready').length,
    [documents],
  );

  async function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    await upload(file, file.name);
    if (fileInput.current) fileInput.current.value = '';
  }

  async function withBusy(id: string, action: () => Promise<void>) {
    setBusyId(id);
    try {
      await action();
    } catch {
      // The hook already records the error message for display.
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <h1 className="text-lg font-semibold text-white">Knowledge base</h1>
            <p className="text-xs text-white/50">
              Documents are chunked, embedded, and retrieved at call time
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="/dashboard/agents"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white/80 hover:bg-white/10"
            >
              Agents
            </a>
            <Button variant="ghost" size="sm" onClick={reload}>
              Refresh
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <div className="grid gap-4 sm:grid-cols-4">
          <GlassCard>
            <div className="text-xs text-white/50">Documents</div>
            <div className="mt-2 text-2xl font-bold text-white">{total}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Searchable</div>
            <div className="mt-2 text-2xl font-bold text-emerald-300">{readyCount}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Indexing</div>
            <div className="mt-2 text-2xl font-bold text-blue-300">{pendingIds.length}</div>
          </GlassCard>
          <GlassCard>
            <div className="text-xs text-white/50">Chunks</div>
            <div className="mt-2 text-2xl font-bold text-white">
              {documents.reduce((sum, doc) => sum + (doc.chunk_count || 0), 0)}
            </div>
          </GlassCard>
        </div>

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Add content</h2>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <div className="rounded-xl border border-dashed border-white/15 p-4">
              <label className="text-xs text-white/60" htmlFor="knowledge-file">
                Upload a document
              </label>
              <input
                id="knowledge-file"
                ref={fileInput}
                type="file"
                onChange={handleFileChange}
                disabled={uploading}
                className="mt-2 block w-full text-xs text-white/70 file:mr-3 file:rounded-lg file:border-0 file:bg-blue-600 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white hover:file:bg-blue-500"
              />
              <p className="mt-2 text-xs text-white/40">
                Ingestion runs in the background; the row below updates when it finishes.
              </p>
            </div>
            <div className="rounded-xl border border-dashed border-white/15 p-4">
              <label className="text-xs text-white/60" htmlFor="knowledge-url">
                Ingest a URL
              </label>
              <div className="mt-2 flex gap-2">
                <input
                  id="knowledge-url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://example.com/pricing"
                  className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
                />
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!url.trim() || uploading}
                  onClick={async () => {
                    await ingestUrl(url.trim());
                    setUrl('');
                  }}
                >
                  {uploading ? 'Working…' : 'Ingest'}
                </Button>
              </div>
              <p className="mt-2 text-xs text-white/40">
                Pages are fetched server-side and chunked like uploaded files.
              </p>
            </div>
          </div>
        </GlassCard>

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Retrieval preview</h2>
          <p className="mt-1 text-xs text-white/50">
            Runs the same retrieval the agent uses, with real similarity scores, so you can see why
            an answer was grounded in a given passage.
          </p>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ask what the agent would look up…"
              aria-label="Retrieval preview query"
              className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none"
            />
            <Button size="sm" variant="primary" disabled={!query.trim() || searching} onClick={() => search(query, 5)}>
              {searching ? 'Searching…' : 'Search'}
            </Button>
            {searchResults.length > 0 && (
              <Button size="sm" variant="ghost" onClick={clearSearch}>
                Clear
              </Button>
            )}
          </div>
          {searchResults.length > 0 && (
            <ul className="mt-4 space-y-2">
              {searchResults.map((hit) => (
                <li key={hit.chunk_id} className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-medium text-white">{hit.title}</span>
                    <span className="text-xs text-white/50">score {hit.score.toFixed(3)}</span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-white/60">{hit.text}</p>
                </li>
              ))}
            </ul>
          )}
        </GlassCard>

        {error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        <GlassCard>
          <h2 className="text-sm font-semibold text-white">Documents</h2>
          {loading ? (
            <div className="mt-4 space-y-2">
              {[0, 1, 2].map((row) => (
                <div key={row} className="h-16 rounded-xl bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : documents.length === 0 ? (
            <p className="mt-4 text-sm text-white/60">
              No documents yet. Upload a file or ingest a URL to give your agents something to
              retrieve.
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-white/5">
              {documents.map((doc) => {
                const status = String(doc.status ?? '').toLowerCase();
                const busy = busyId === doc.id || pendingIds.includes(doc.id);
                return (
                  <li key={doc.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-medium text-white">{doc.title}</span>
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${statusClass(status)}`}>
                          {busy && status !== 'failed' ? 'indexing' : status || 'unknown'}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-white/45">
                        <span>{doc.original_filename || doc.source_type}</span>
                        <span>{formatBytes(doc.file_size)}</span>
                        <span>{doc.chunk_count} chunks</span>
                        <span>{doc.token_estimate} tokens (est.)</span>
                        {doc.embedding_model ? <span>{doc.embedding_model}</span> : null}
                      </div>
                      {doc.error_message ? (
                        <p className="mt-1 text-xs text-red-300">{doc.error_message}</p>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => withBusy(doc.id, () => reindex(doc.id))}
                      >
                        Reindex
                      </Button>
                      {status === 'archived' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => withBusy(doc.id, () => restore(doc.id))}
                        >
                          Restore
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => withBusy(doc.id, () => remove(doc.id))}
                        >
                          Archive
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </GlassCard>

        {stats ? (
          <GlassCard>
            <h2 className="text-sm font-semibold text-white">Tenant knowledge statistics</h2>
            <pre className="mt-3 overflow-x-auto rounded-xl bg-black/40 p-3 text-xs text-white/60">
              {JSON.stringify(stats, null, 2)}
            </pre>
          </GlassCard>
        ) : null}
      </div>
    </div>
  );
}

export default AgentKnowledgePage;
