/**
 * dashboard/src/api/knowledge.ts
 * Workspace-level knowledge base: documents, ingestion, retrieval preview, stats.
 *
 * Route source: app/api/knowledge_routes.py — `APIRouter(prefix="/api/knowledge")`.
 * Types mirror that module's models (`DocumentOut`, `DocumentListOut`,
 * `SearchRequest`/`SearchHit`/`SearchResponse`, `UrlIngestRequest`, and the
 * literal dict returned by `knowledge_stats`).
 *
 * Relationship to `api/agent-knowledge.ts`: that client is the agent-scoped view
 * used inside the Agent Studio (same `/api/knowledge/*` routes, filtered by the
 * agent's workspace). This module is the workspace-level console surface and
 * adds the lifecycle calls the Studio page does not use (restore, hard delete,
 * stats, URL ingestion). Both point at one router — there is no second store.
 */
import { client } from './client';

export interface KnowledgeDocument {
  id: string;
  title: string;
  status: string;
  source_type: string;
  original_filename?: string | null;
  file_type?: string | null;
  file_size?: number;
  version?: number;
  chunk_count?: number;
  char_count?: number;
  token_estimate?: number;
  embedding_model?: string | null;
  embedding_dimensions?: number | null;
  error_message?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  indexed_at?: string | null;
  is_searchable?: boolean;
}

export interface DocumentList {
  documents: KnowledgeDocument[];
  total: number;
  limits: Record<string, unknown>;
}

export interface SearchHit {
  document_id: string;
  chunk_id: string;
  title: string;
  text: string;
  score: number;
  page?: number | null;
  heading?: string | null;
}

export interface SearchResponse {
  query: string;
  results: SearchHit[];
  count: number;
}

export interface KnowledgeStats {
  documents: Record<string, number>;
  total_documents: number;
  total_chunks: number;
  searchable_chunks: number;
  embedding_model: string;
  embedding_dimensions: number;
}

export function listDocuments(params: { status?: string | null; limit?: number; offset?: number } = {}): Promise<DocumentList> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return client.get<DocumentList>(`/api/knowledge/documents${suffix}`);
}

export function getDocument(documentId: string): Promise<KnowledgeDocument> {
  return client.get<KnowledgeDocument>(`/api/knowledge/documents/${encodeURIComponent(documentId)}`);
}

/**
 * Upload a file. The route is `multipart/form-data` with a required `file`
 * field plus optional `title` and `environment_id` form fields, and answers
 * `202` because indexing continues in the background.
 */
export function uploadDocument(
  file: File,
  options: { title?: string; environmentId?: string } = {},
): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append('file', file, file.name);
  if (options.title) form.append('title', options.title);
  if (options.environmentId) form.append('environment_id', options.environmentId);
  return client.post<KnowledgeDocument>('/api/knowledge/documents', form, {
    raw: true,
    headers: {}, // let the browser set the multipart boundary
  });
}

/** `hard: false` archives (recoverable); `hard: true` destroys file, chunks and row. */
export function deleteDocument(documentId: string, hard = false): Promise<Record<string, unknown>> {
  return client.del<Record<string, unknown>>(
    `/api/knowledge/documents/${encodeURIComponent(documentId)}?hard=${hard ? 'true' : 'false'}`,
  );
}

export function reindexDocument(documentId: string): Promise<KnowledgeDocument> {
  return client.post<KnowledgeDocument>(`/api/knowledge/documents/${encodeURIComponent(documentId)}/reindex`);
}

export function restoreDocument(documentId: string): Promise<KnowledgeDocument> {
  return client.post<KnowledgeDocument>(`/api/knowledge/documents/${encodeURIComponent(documentId)}/restore`);
}

export function searchKnowledge(query: string, topK?: number, environmentId?: string): Promise<SearchResponse> {
  return client.post<SearchResponse>('/api/knowledge/search', {
    query,
    ...(topK !== undefined ? { top_k: topK } : {}),
    ...(environmentId ? { environment_id: environmentId } : {}),
  });
}

export function ingestUrl(url: string, options: { maxDepth?: number; maxPages?: number; environmentId?: string } = {}): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>('/api/knowledge/urls', {
    url,
    ...(options.maxDepth !== undefined ? { max_depth: options.maxDepth } : {}),
    ...(options.maxPages !== undefined ? { max_pages: options.maxPages } : {}),
    ...(options.environmentId ? { environment_id: options.environmentId } : {}),
  });
}

export function knowledgeStats(): Promise<KnowledgeStats> {
  return client.get<KnowledgeStats>('/api/knowledge/stats');
}
