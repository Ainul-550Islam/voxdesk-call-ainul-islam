/**
 * dashboard/src/api/agent-knowledge.ts
 *
 * Knowledge-base API client.
 *
 * Every path below is served by `app/api/knowledge_routes.py`:
 *
 *   GET    /api/knowledge/documents                 list (tenant-scoped)
 *   POST   /api/knowledge/documents                 upload (multipart)
 *   GET    /api/knowledge/documents/{id}            one document
 *   DELETE /api/knowledge/documents/{id}            archive (or purge with ?hard=true)
 *   POST   /api/knowledge/documents/{id}/reindex
 *   POST   /api/knowledge/documents/{id}/restore
 *   POST   /api/knowledge/search                    retrieval preview
 *   GET    /api/knowledge/stats
 *   POST   /api/knowledge/urls                      ingest a URL
 *
 * The previous version of this file called `/api/agents/{id}/agent-knowledge`
 * and `/api/agent-knowledge`, neither of which exists. Both calls were wrapped
 * in `catch { return [] }`, so the UI silently rendered an empty knowledge
 * list instead of surfacing the 404. These wrappers propagate failures and
 * return explicit, typed shapes instead.
 */

import { apiClient } from './client';

/** The tenant-visible view of a document, mirroring `DocumentOut`. */
export interface KnowledgeDocument {
  id: string;
  title: string;
  status: string;
  source_type: string;
  original_filename: string | null;
  file_type: string | null;
  file_size: number;
  version: number;
  chunk_count: number;
  char_count: number;
  token_estimate: number;
  embedding_model: string | null;
  embedding_dimensions: number | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
  indexed_at: string | null;
  is_searchable: boolean;
}

export interface DocumentListResponse {
  documents: KnowledgeDocument[];
  total: number;
  limits: Record<string, unknown>;
}

export interface KnowledgeStats {
  [key: string]: unknown;
}

export interface SearchHit {
  document_id: string;
  chunk_id: string;
  title: string;
  text: string;
  score: number;
  page: number | null;
  heading: string | null;
}

export interface SearchResponse {
  query: string;
  results: SearchHit[];
  count: number;
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export interface ListDocumentsParams {
  status?: string;
  limit?: number;
  offset?: number;
}

export async function listAgentKnowledge(
  params: ListDocumentsParams = {},
): Promise<DocumentListResponse> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (typeof params.limit === 'number') qs.set('limit', String(params.limit));
  if (typeof params.offset === 'number') qs.set('offset', String(params.offset));
  const query = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<DocumentListResponse>(`/api/knowledge/documents${query}`);
  return {
    documents: asArray<KnowledgeDocument>(res?.documents),
    total: typeof res?.total === 'number' ? res.total : asArray<KnowledgeDocument>(res?.documents).length,
    limits: res?.limits ?? {},
  };
}

export async function getAgentKnowledge(documentId: string): Promise<KnowledgeDocument> {
  return apiClient.get<KnowledgeDocument>(
    `/api/knowledge/documents/${encodeURIComponent(documentId)}`,
  );
}

export interface UploadKnowledgeInput {
  file: File;
  title?: string;
  environmentId?: string;
}

/**
 * Upload a document for ingestion.
 *
 * The backend answers 202 immediately and indexes out of band, so callers
 * poll `getAgentKnowledge` for the transition to `ready` / `failed`.
 */
export async function uploadAgentKnowledge(
  input: UploadKnowledgeInput,
): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append('file', input.file);
  if (input.title) form.append('title', input.title);
  if (input.environmentId) form.append('environment_id', input.environmentId);
  return apiClient.post<KnowledgeDocument>(
    '/api/knowledge/documents',
    form,
    { raw: true } as never,
  );
}

export async function deleteAgentKnowledge(
  documentId: string,
  hard = false,
): Promise<void> {
  const suffix = hard ? '?hard=true' : '';
  await apiClient.delete(`/api/knowledge/documents/${encodeURIComponent(documentId)}${suffix}`);
}

export async function reindexAgentKnowledge(
  documentId: string,
): Promise<KnowledgeDocument> {
  return apiClient.post<KnowledgeDocument>(
    `/api/knowledge/documents/${encodeURIComponent(documentId)}/reindex`,
    {},
  );
}

export async function restoreAgentKnowledge(
  documentId: string,
): Promise<KnowledgeDocument> {
  return apiClient.post<KnowledgeDocument>(
    `/api/knowledge/documents/${encodeURIComponent(documentId)}/restore`,
    {},
  );
}

export async function searchAgentKnowledge(
  query: string,
  topK?: number,
): Promise<SearchResponse> {
  const body: Record<string, unknown> = { query };
  if (typeof topK === 'number') body.top_k = topK;
  return apiClient.post<SearchResponse>('/api/knowledge/search', body);
}

export async function getAgentKnowledgeStats(): Promise<KnowledgeStats> {
  return apiClient.get<KnowledgeStats>('/api/knowledge/stats');
}

export async function ingestKnowledgeUrl(url: string, title?: string): Promise<KnowledgeDocument> {
  const body: Record<string, unknown> = { url };
  if (title) body.title = title;
  return apiClient.post<KnowledgeDocument>('/api/knowledge/urls', body);
}
