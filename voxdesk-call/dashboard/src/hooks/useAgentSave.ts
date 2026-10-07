/**
 * dashboard/src/hooks/useAgentSave.ts
 *
 * Draft autosave for the Agent Builder with optimistic concurrency.
 *
 * Backed by:
 *   GET   /api/v1/agents/{agent_id}/builder   (read draft + ETag)
 *   PATCH /api/v1/agents/{agent_id}/builder   (write draft, `If-Match` ETag)
 *
 * The previous version of this hook was a placeholder: it set
 * `data = { agentId }` and never issued a request, so "saved" was a state the
 * UI could display without a single byte reaching the server.
 *
 * Concurrency behaviour is explicit and honest:
 * - every write sends `If-Match` with the ETag the draft was loaded with;
 * - a 409 from the server is reported as `conflict: true` and sets
 *   `serverEtag`, so the UI can offer "reload theirs" / "overwrite mine";
 * - a 409 is never silently retried, because retrying would discard the other
 *   editor's work.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, apiClient } from '../api/client';
import { classifyConflict, getAgentDraftIdentity } from '../api/agent-conflict';

export interface BuilderDraft {
  identity?: Record<string, unknown>;
  voice?: Record<string, unknown>;
  model?: Record<string, unknown>;
  knowledge_bases?: unknown[];
  tools?: unknown[];
  call_handling?: Record<string, unknown>;
  security?: Record<string, unknown>;
  [key: string]: unknown;
}

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error';

export interface UseAgentSaveResult {
  draft: BuilderDraft | null;
  etag: string;
  state: SaveState;
  dirty: boolean;
  error: string | null;
  conflict: boolean;
  serverEtag: string | null;
  lastSavedAt: string | null;
  load: () => Promise<void>;
  update: (patch: BuilderDraft) => void;
  save: () => Promise<boolean>;
  /** Discard local edits and reload the server's current draft. */
  reloadFromServer: () => Promise<void>;
}

export function useAgentSave(agentId?: string): UseAgentSaveResult {
  const [draft, setDraft] = useState<BuilderDraft | null>(null);
  const [etag, setEtag] = useState('');
  const [state, setState] = useState<SaveState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [serverEtag, setServerEtag] = useState<string | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const mounted = useRef(true);
  const baseline = useRef<string>('');

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    if (!agentId) {
      setDraft(null);
      setState('idle');
      return;
    }
    setState('saving');
    setError(null);
    try {
      const res = await apiClient.get<BuilderDraft & Record<string, unknown>>(
        `/api/v1/agents/${encodeURIComponent(agentId)}/builder`,
      );
      if (!mounted.current) return;
      const nextDraft: BuilderDraft = {
        identity: (res.identity as Record<string, unknown>) ?? undefined,
        voice: (res.voice as Record<string, unknown>) ?? undefined,
        model: (res.model as Record<string, unknown>) ?? undefined,
        knowledge_bases: (res.knowledge_bases as unknown[]) ?? [],
        tools: (res.tools as unknown[]) ?? [],
        call_handling: (res.call_handling as Record<string, unknown>) ?? undefined,
        security: (res.security as Record<string, unknown>) ?? undefined,
      };
      const nextEtag = String(res.etag ?? res.draft_etag ?? '');
      setDraft(nextDraft);
      setEtag(nextEtag);
      baseline.current = JSON.stringify(nextDraft);
      setConflict(false);
      setServerEtag(null);
      setState('idle');
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load draft');
      setState('error');
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = useCallback((patch: BuilderDraft) => {
    setDraft((current) => {
      const next = { ...(current ?? {}), ...patch };
      const isDirty = JSON.stringify(next) !== baseline.current;
      setState((currentState) =>
        currentState === 'conflict' ? 'conflict' : isDirty ? 'dirty' : 'idle',
      );
      return next;
    });
    setError(null);
  }, []);

  const save = useCallback(async () => {
    if (!agentId || !draft) return false;
    setState('saving');
    setError(null);
    try {
      const res = await apiClient.patch<Record<string, unknown>>(
        `/api/v1/agents/${encodeURIComponent(agentId)}/builder`,
        { ...draft, expected_etag: etag || undefined },
        etag ? { headers: { 'If-Match': etag } } : undefined,
      );
      if (!mounted.current) return true;
      const nextEtag = String(res?.etag ?? res?.draft_etag ?? etag);
      setEtag(nextEtag);
      baseline.current = JSON.stringify(draft);
      setConflict(false);
      setServerEtag(null);
      setLastSavedAt(new Date().toISOString());
      setState('saved');
      return true;
    } catch (err) {
      if (!mounted.current) return false;
      const classification = classifyConflict(err);
      if (classification.isConflict) {
        setConflict(true);
        setServerEtag(classification.serverEtag);
        setError('This agent was changed by someone else. Reload or overwrite to continue.');
        setState('conflict');
        return false;
      }
      setError(
        err instanceof ApiError || err instanceof Error
          ? (err as Error).message
          : 'Failed to save draft',
      );
      setState('error');
      return false;
    }
  }, [agentId, draft, etag]);

  const reloadFromServer = useCallback(async () => {
    await load();
  }, [load]);

  return {
    draft,
    etag,
    state,
    dirty: state === 'dirty',
    error,
    conflict,
    serverEtag,
    lastSavedAt,
    load,
    update,
    save,
    reloadFromServer,
  };
}

export { getAgentDraftIdentity };
export default useAgentSave;
