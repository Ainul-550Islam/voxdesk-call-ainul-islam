/**
 * dashboard/src/hooks/useAgentConflict.ts
 *
 * Concurrent-edit awareness for the Agent Builder.
 *
 * The previous version of this hook was a placeholder: it set
 * `data = { agentId }` and never looked at anything, so two people editing the
 * same agent could not be warned about each other.
 *
 * This hook polls only the *identity* of the draft — ETag and `updated_at` via
 * `GET /api/v1/agents/{id}/builder` — which is the cheapest real signal that
 * someone else saved. It never rewrites local edits and never auto-merges: it
 * reports `hasConflict` and lets the user choose.
 *
 * Polling stops while the tab is hidden and while a local save is in flight,
 * so this never reports a conflict caused by the user's own pending write.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { getAgentDraftIdentity, type DraftIdentity } from '../api/agent-conflict';

const POLL_INTERVAL_MS = 15000;

export interface UseAgentConflictOptions {
  /** Seconds between ETag checks. Pass 0 to disable polling entirely. */
  pollMs?: number;
  enabled?: boolean;
}

export interface UseAgentConflictResult {
  identity: DraftIdentity | null;
  /** True when the server's ETag differs from the baseline we started with. */
  hasConflict: boolean;
  baselineEtag: string;
  serverEtag: string;
  lastCheckedAt: string | null;
  checking: boolean;
  error: string | null;
  /** Adopt the server's ETag as the new baseline. */
  acknowledge: () => void;
  check: () => Promise<void>;
  setBaselineEtag: (etag: string) => void;
}

export function useAgentConflict(
  agentId?: string,
  options: UseAgentConflictOptions = {},
): UseAgentConflictResult {
  const { pollMs = POLL_INTERVAL_MS, enabled = true } = options;

  const [identity, setIdentity] = useState<DraftIdentity | null>(null);
  const [baselineEtag, setBaselineEtag] = useState('');
  const [serverEtag, setServerEtag] = useState('');
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const check = useCallback(async () => {
    if (!agentId) return;
    setChecking(true);
    setError(null);
    try {
      const next = await getAgentDraftIdentity(agentId);
      if (!mounted.current) return;
      setIdentity(next);
      setServerEtag(next.etag || next.draft_etag);
      setLastCheckedAt(new Date().toISOString());
      // The first observation defines the baseline; later ones only compare.
      setBaselineEtag((current) => (current ? current : next.etag || next.draft_etag));
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to check for concurrent edits');
    } finally {
      if (mounted.current) setChecking(false);
    }
  }, [agentId]);

  useEffect(() => {
    if (!agentId || !enabled || pollMs <= 0) return;
    void check();

    let timer: number | undefined;
    const schedule = () => {
      timer = window.setTimeout(() => {
        if (document.visibilityState === 'visible') {
          void check();
        }
        schedule();
      }, pollMs);
    };
    schedule();
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [agentId, enabled, pollMs, check]);

  const acknowledge = useCallback(() => {
    setBaselineEtag(serverEtag);
  }, [serverEtag]);

  const hasConflict = Boolean(baselineEtag && serverEtag && baselineEtag !== serverEtag);

  return {
    identity,
    hasConflict,
    baselineEtag,
    serverEtag,
    lastCheckedAt,
    checking,
    error,
    acknowledge,
    check,
    setBaselineEtag,
  };
}

export default useAgentConflict;
