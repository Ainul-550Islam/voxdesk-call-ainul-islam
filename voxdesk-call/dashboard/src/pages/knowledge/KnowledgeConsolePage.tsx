/**
 * dashboard/src/pages/knowledge/KnowledgeConsolePage.tsx
 * Workspace knowledge base: upload, index, search, delete/restore.
 *
 * Data: `api/knowledge.ts` →
 *   GET    /api/knowledge/documents          (list, status + limit/offset)
 *   POST   /api/knowledge/documents          (multipart `file`, 202)
 *   POST   /api/knowledge/documents/{id}/reindex
 *   DELETE /api/knowledge/documents/{id}?hard=
 *   POST   /api/knowledge/documents/{id}/restore
 *   POST   /api/knowledge/search             ({query, top_k})
 *   GET    /api/knowledge/stats
 *
 * Upload is a real multipart POST to the same router the Studio uses; the file
 * never leaves the browser until `Upload` is pressed. Search returns only hits
 * the retriever actually produced — an empty result is rendered as such rather
 * than filled with example text.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  KnowledgeDocument,
  SearchHit,
  KnowledgeStats,
  deleteDocument,
  ingestUrl,
  knowledgeStats,
  listDocuments,
  reindexDocument,
  restoreDocument,
  searchKnowledge,
  uploadDocument,
} from '../../api/knowledge';
import {
  Badge,
  ConsoleCard,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  buttonStyle,
  formatDate,
  inputStyle,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

const PAGE_SIZE = 25;
const STATUSES = ['', 'pending', 'processing', 'indexed', 'failed', 'archived'] as const;

function humanBytes(size: number | null): string {
  if (size === null || size === undefined) return '—';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function KnowledgeConsolePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('');
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [title, setTitle] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [ingestTarget, setIngestTarget] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [pageResult, statsResult] = await Promise.all([
        listDocuments({ status: status || null, limit: PAGE_SIZE, offset }),
        knowledgeStats(),
      ]);
      setDocuments(pageResult.documents);
      setTotal(pageResult.total);
      setStats(statsResult);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [status, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) {
      setActionError(new Error('Choose a file first.'));
      return;
    }
    void run('upload', async () => {
      const accepted = await uploadDocument(file, { title: title.trim() || file.name });
      setNotice(`Queued ${accepted.title} (${accepted.status}). Indexing continues server-side.`);
      setFile(null);
      setTitle('');
      await load();
    });
  }

  function ingest(event: React.FormEvent) {
    event.preventDefault();
    void run('ingest', async () => {
      const accepted = await ingestUrl(ingestTarget.trim(), { maxDepth: 1, maxPages: 10 });
      setNotice(
        `Crawl queued for ${String(accepted.origin_url ?? ingestTarget)}: up to ${String(accepted.page_limit ?? 10)} page(s) will be fetched.`,
      );
      setIngestTarget('');
      await load();
    });
  }

  function search(event: React.FormEvent) {
    event.preventDefault();
    setSearching(true);
    setActionError(null);
    void (async () => {
      try {
        const response = await searchKnowledge(query.trim(), 8);
        setHits(response.results);
      } catch (e) {
        setActionError(e);
        setHits(null);
      } finally {
        setSearching(false);
      }
    })();
  }

  if (loading && documents.length === 0 && !error) return <LoadingState label="Loading knowledge base…" />;

  return (
    <main data-testid="knowledge-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Knowledge base</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Documents the assistant may retrieve from, scoped to this workspace.
        {stats && ` ${stats.total_documents} document(s), ${stats.searchable_chunks} searchable chunk(s) · ${stats.embedding_model} (${stats.embedding_dimensions}d)`}
      </p>

      <ErrorState error={error} onRetry={() => void load()} />
      <ErrorState error={actionError} />
      {notice && (
        <div role="status" data-testid="knowledge-notice" style={{ background: '#0f2418', border: '1px solid #1d4d31', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 13, color: '#c9f3da' }}>
          {notice}
        </div>
      )}

      <ConsoleCard title="Add a document" testId="knowledge-upload">
        <form onSubmit={upload}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
            <Field label="File">
              <input style={inputStyle} type="file" data-testid="knowledge-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </Field>
            <Field label="Title (optional)">
              <input style={inputStyle} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file?.name ?? 'document title'} />
            </Field>
          </div>
          <button type="submit" style={buttonStyle} disabled={busy === 'upload' || !file} data-testid="knowledge-upload-submit">
            {busy === 'upload' ? 'Uploading…' : 'Upload'}
          </button>
        </form>

        <form onSubmit={ingest} style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 280px' }}>
            <Field label="Or crawl a public URL">
              <input style={inputStyle} value={ingestTarget} onChange={(e) => setIngestTarget(e.target.value)} placeholder="https://docs.example.com/faq" />
            </Field>
          </div>
          <button type="submit" style={subtleButtonStyle} disabled={busy === 'ingest' || !ingestTarget.trim()}>
            {busy === 'ingest' ? 'Queuing…' : 'Start crawl'}
          </button>
        </form>
      </ConsoleCard>

      <ConsoleCard title="Search" testId="knowledge-search" description="Runs the same retriever the assistant uses; hits are the real retrieval result.">
        <form onSubmit={search} style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <input
            style={{ ...inputStyle, flex: 1 }}
            data-testid="knowledge-query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="How do refunds work?"
          />
          <button type="submit" style={buttonStyle} disabled={searching || !query.trim()} data-testid="knowledge-search-submit">
            {searching ? 'Searching…' : 'Search'}
          </button>
        </form>
        {hits === null ? (
          <p style={{ fontSize: 12.5, color: '#8fa3b8', margin: 0 }}>No query run yet.</p>
        ) : hits.length === 0 ? (
          <EmptyState title="No matches" detail="The retriever returned zero chunks above threshold for this query." />
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {hits.map((hit) => (
              <li key={hit.chunk_id} style={{ borderTop: '1px solid #1d2836', padding: '8px 0' }}>
                <div style={{ fontSize: 12.5 }}>
                  <strong>{hit.title}</strong>{' '}
                  <span style={{ color: '#8fa3b8' }}>
                    score {hit.score.toFixed(3)}
                    {hit.page !== null && ` · p${hit.page}`}
                    {hit.heading && ` · ${hit.heading}`}
                  </span>
                </div>
                <p style={{ fontSize: 12, color: '#c8d6e5', margin: '4px 0 0', whiteSpace: 'pre-wrap' }}>{hit.text}</p>
              </li>
            ))}
          </ul>
        )}
      </ConsoleCard>

      <ConsoleCard
        title={`Documents (${total})`}
        testId="knowledge-documents"
        actions={
          <select style={{ ...inputStyle, width: 150, padding: '4px 6px' }} value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} data-testid="knowledge-status-filter">
            {STATUSES.map((value) => (
              <option key={value || 'all'} value={value}>
                {value || 'all statuses'}
              </option>
            ))}
          </select>
        }
      >
        {documents.length === 0 ? (
          <EmptyState title="No documents" detail="Upload a file or crawl a URL to give the assistant something to retrieve." />
        ) : (
          <>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Title</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Type</th>
                  <th style={thStyle}>Size</th>
                  <th style={thStyle}>Chunks</th>
                  <th style={thStyle}>Updated</th>
                  <th style={thStyle} />
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <tr key={document.id} data-testid={`knowledge-row-${document.id}`}>
                    <td style={tdStyle}>
                      {document.title}
                      {document.error_message && <div style={{ fontSize: 10.5, color: '#f2a1a1' }}>{document.error_message}</div>}
                    </td>
                    <td style={tdStyle}>
                      <Badge tone={document.status === 'indexed' ? 'ok' : document.status === 'failed' ? 'bad' : 'warn'}>{document.status}</Badge>
                    </td>
                    <td style={tdStyle}>{document.file_type ?? document.source_type}</td>
                    <td style={tdStyle}>{humanBytes(document.file_size)}</td>
                    <td style={tdStyle}>{document.chunk_count}</td>
                    <td style={tdStyle}>{formatDate(document.updated_at)}</td>
                    <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                      <button
                        style={subtleButtonStyle}
                        disabled={busy !== null}
                        onClick={() =>
                          void run(`reindex:${document.id}`, async () => {
                            const updated = await reindexDocument(document.id);
                            setNotice(`Re-index queued for ${updated.title} (${updated.status}).`);
                            await load();
                          })
                        }
                      >
                        Reindex
                      </button>{' '}
                      {document.status === 'archived' ? (
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`restore:${document.id}`, async () => {
                              await restoreDocument(document.id);
                              setNotice(`Restored ${document.title}.`);
                              await load();
                            })
                          }
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`delete:${document.id}`, async () => {
                              await deleteDocument(document.id, false);
                              setNotice(`Archived ${document.title}; chunks are no longer searchable.`);
                              await load();
                            })
                          }
                        >
                          Archive
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
              <span style={{ fontSize: 11.5, color: '#8fa3b8' }}>
                Showing {offset + 1}–{offset + documents.length} of {total}
              </span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button style={subtleButtonStyle} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  Previous
                </button>
                <button style={subtleButtonStyle} disabled={offset + documents.length >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </ConsoleCard>
    </main>
  );
}

export default KnowledgeConsolePage;
