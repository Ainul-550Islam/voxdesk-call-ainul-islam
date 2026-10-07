/**
 * dashboard/src/hooks/useAgentKnowledge.ts
 *
 * Knowledge-base state for the Agent Studio knowledge screen.
 *
 * Backed by `app/api/knowledge_routes.py` through `api/agent-knowledge.ts`.
 * The previous version of this hook was a placeholder: it set
 * `data = { agentId }` and never issued a request, so the knowledge screen
 * could never show a document, an error, or a loading state.
 *
 * Upload is asynchronous on the server (202 + background ingestion), so this
 * hook polls the uploaded document until it leaves the `uploaded` /
 * `processing` states, up to a bounded number of attempts.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  deleteAgentKnowledge,
  getAgentKnowledge,
  getAgentKnowledgeStats,
  ingestKnowledgeUrl,
  listAgentKnowledge,
  reindexAgentKnowledge,
  restoreAgentKnowledge,
  searchAgentKnowledge,
  uploadAgentKnowledge,
  type KnowledgeDocument,
  type KnowledgeStats,
  type SearchHit,
} from '../api/agent-knowledge';

const TERMINAL_STATUSES = new Set(['ready', 'failed', 'archived', 'deleted']);
const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 40;

export interface UseAgentKnowledgeResult {
  documents: KnowledgeDocument[];
  total: number;
  stats: KnowledgeStats | null;
  loading: boolean;
  error: string | null;
  /** Set while an upload is in flight or being polled to a terminal state. */
  uploading: boolean;
  /** Document ids currently being polled after upload/reindex. */
  pendingIds: string[];
  searchResults: SearchHit[];
  searching: boolean;
  reload: () => Promise<void>;
  upload: (file: File, title?: string) => Promise<KnowledgeDocument | null>;
  remove: (documentId: string, hard?: boolean) => Promise<void>;
  reindex: (documentId: string) => Promise<void>;
  restore: (documentId: string) => Promise<void>;
  ingestUrl: (url: string, title?: string) => Promise<void>;
  search: (query: string, topK?: number) => Promise<SearchHit[]>;
  clearSearch: () => void;
}

export function useAgentKnowledge(): UseAgentKnowledgeResult {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [pendingIds, setPendingIds] = useState<string[]>([]);
  const [searchResults, setSearchResults] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, statsRes] = await Promise.all([
        listAgentKnowledge({ limit: 200 }),
        getAgentKnowledgeStats().catch(() => null),
      ]);
      if (!mounted.current) return;
      setDocuments(list.documents);
      setTotal(list.total);
      setStats(statsRes);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load knowledge base');
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Poll a document until ingestion reaches a terminal state. */
  const pollUntilSettled = useCallback(async (documentId: string) => {
    setPendingIds((current) => (current.includes(documentId) ? current : [...current, documentId]));
    try {
      for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        if (!mounted.current) return;
        const document = await getAgentKnowledge(documentId);
        if (!mounted.current) return;
        setDocuments((current) => {
          const index = current.findIndex((item) => item.id === document.id);
          if (index === -1) return [document, ...current];
          const next = [...current];
          next[index] = document;
          return next;
        });
        if (TERMINAL_STATUSES.has(String(document.status ?? '').toLowerCase())) return;
      }
    } catch {
      // A failed poll is not fatal: the list refresh below will surface the
      // document's real state on the next reload.
    } finally {
      if (mounted.current) {
        setPendingIds((current) => current.filter((id) => id !== documentId));
      }
    }
  }, []);

  const upload = useCallback(
    async (file: File, title?: string) => {
      setUploading(true);
      setError(null);
      try {
        const created = await uploadAgentKnowledge({ file, title });
        if (!mounted.current) return created;
        setDocuments((current) => [created, ...current.filter((item) => item.id !== created.id)]);
        setTotal((current) => current + 1);
        void pollUntilSettled(created.id);
        return created;
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Upload failed');
        }
        return null;
      } finally {
        if (mounted.current) setUploading(false);
      }
    },
    [pollUntilSettled],
  );

  const remove = useCallback(async (documentId: string, hard = false) => {
    setError(null);
    try {
      await deleteAgentKnowledge(documentId, hard);
      if (!mounted.current) return;
      setDocuments((current) => current.filter((item) => item.id !== documentId));
      setTotal((current) => Math.max(0, current - 1));
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : 'Delete failed');
      }
      throw err;
    }
  }, []);

  const reindex = useCallback(
    async (documentId: string) => {
      setError(null);
      try {
        const updated = await reindexAgentKnowledge(documentId);
        if (!mounted.current) return;
        setDocuments((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        );
        void pollUntilSettled(updated.id);
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'Reindex failed');
        }
        throw err;
      }
    },
    [pollUntilSettled],
  );

  const restore = useCallback(async (documentId: string) => {
    setError(null);
    try {
      const updated = await restoreAgentKnowledge(documentId);
      if (!mounted.current) return;
      setDocuments((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : 'Restore failed');
      }
      throw err;
    }
  }, []);

  const ingestUrl = useCallback(
    async (url: string, title?: string) => {
      setUploading(true);
      setError(null);
      try {
        const created = await ingestKnowledgeUrl(url, title);
        if (!mounted.current) return;
        setDocuments((current) => [created, ...current.filter((item) => item.id !== created.id)]);
        setTotal((current) => current + 1);
        void pollUntilSettled(created.id);
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : 'URL ingest failed');
        }
        throw err;
      } finally {
        if (mounted.current) setUploading(false);
      }
    },
    [pollUntilSettled],
  );

  const search = useCallback(async (query: string, topK?: number) => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResults([]);
      return [];
    }
    setSearching(true);
    setError(null);
    try {
      const res = await searchAgentKnowledge(trimmed, topK);
      if (!mounted.current) return [];
      setSearchResults(res.results);
      return res.results;
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : 'Search failed');
      }
      return [];
    } finally {
      if (mounted.current) setSearching(false);
    }
  }, []);

  const clearSearch = useCallback(() => {
    setSearchResults([]);
  }, []);

  return {
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
  };
}

export default useAgentKnowledge;
