/**
 * dashboard/src/api/agent-conflict.ts
 *
 * Optimistic-concurrency (ETag / 409) helpers for the Agent Builder.
 *
 * The builder saves through `PATCH /api/v1/agents/{agent_id}/builder` with an
 * `If-Match` header, and the backend answers **409 Conflict** when the draft
 * changed underneath the editor. That is a real condition produced by the
 * server, not something to simulate, so this module only does two things:
 *
 *   1. reads the current draft ETag (`GET /api/v1/agents/{id}/builder`)
 *   2. classifies an error as a conflict so the UI can offer
 *      "reload theirs" / "overwrite with mine" instead of losing work
 *
 * The previous version of this file called `/api/agents/{id}/agent-conflict`,
 * which does not exist, and returned `null` — so concurrency conflicts were
 * silently indistinguishable from a network outage.
 */

import { ApiError, apiClient } from './client';

export interface DraftIdentity {
  agent_id: string;
  etag: string;
  draft_etag: string;
  version: number;
  lock_version: number;
  updated_at: string;
}

export async function getAgentDraftIdentity(agentId: string): Promise<DraftIdentity> {
  const res = await apiClient.get<Record<string, unknown>>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/builder`,
  );
  return {
    agent_id: String(res?.agent_id ?? res?.id ?? agentId),
    etag: String(res?.etag ?? res?.draft_etag ?? ''),
    draft_etag: String(res?.draft_etag ?? res?.etag ?? ''),
    version: Number(res?.version ?? 0),
    lock_version: Number(res?.lock_version ?? 1),
    updated_at: String(res?.updated_at ?? ''),
  };
}

export interface ConflictClassification {
  isConflict: boolean;
  status: number;
  message: string;
  /** The server's current ETag, when it sent one back. */
  serverEtag: string | null;
}

/**
 * Decide whether a failed save is a concurrency conflict.
 *
 * Only a 409 (or an explicit `If-Match`/etag mismatch message) counts. A 5xx
 * or a network error is NOT a conflict: telling the user "someone else edited
 * this" when the real cause was an outage would lose their work twice.
 */
export function classifyConflict(error: unknown): ConflictClassification {
  const status = error instanceof ApiError ? error.status : 0;
  const message =
    error instanceof Error ? error.message : typeof error === 'string' ? error : 'Unknown error';
  const detail =
    error instanceof ApiError && error.detail && typeof error.detail === 'object'
      ? (error.detail as Record<string, unknown>)
      : null;
  const lower = message.toLowerCase();
  const isConflict =
    status === 409 ||
    status === 412 ||
    lower.includes('etag') ||
    lower.includes('conflict') ||
    lower.includes('stale');
  return {
    isConflict,
    status,
    message,
    serverEtag: detail && typeof detail.etag === 'string' ? detail.etag : null,
  };
}
