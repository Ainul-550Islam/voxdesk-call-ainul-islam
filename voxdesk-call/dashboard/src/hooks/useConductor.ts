import { useCallback, useEffect, useState } from 'react';
import {
  applyProposal,
  approveChange,
  approveProposal,
  createConductorSession,
  getConductorProposal,
  getConductorSession,
  getProposalDiff,
  listConductorProposals,
  listConductorSessions,
  rejectChange,
  rejectProposal,
  simulateProposal,
  submitConductorRequest,
  undoChangeApproval,
  undoProposalApproval,
  validateProposal,
} from '../api/conductor';
import type {
  ConductorApplyPayload,
  ConductorApplyResult,
  ConductorPromptPayload,
  ConductorProposal,
  ConductorProposalDiff,
  ConductorSession,
  ConductorSessionCreatePayload,
  ConductorSimulatePayload,
} from '../api/types/conductor';

export interface UseConductorOptions {
  agentId?: string;
  agentKind?: 'voice' | 'chat';
  autoLoad?: boolean;
}

export function useConductor(options?: UseConductorOptions) {
  const [sessions, setSessions] = useState<ConductorSession[]>([]);
  const [activeSession, setActiveSession] = useState<ConductorSession | null>(
    null
  );
  const [proposals, setProposals] = useState<ConductorProposal[]>([]);
  const [activeProposal, setActiveProposal] =
    useState<ConductorProposal | null>(null);
  const [activeDiff, setActiveDiff] = useState<ConductorProposalDiff | null>(
    null
  );
  const [applyResult, setApplyResult] = useState<ConductorApplyResult | null>(
    null
  );
  const [loading, setLoading] = useState<boolean>(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadProposalAndDiff = useCallback(async (proposalId: string) => {
    const [prop, diff] = await Promise.all([
      getConductorProposal(proposalId),
      getProposalDiff(proposalId),
    ]);
    setActiveProposal(prop);
    setActiveDiff(diff);
    setProposals((prev) => {
      const exists = prev.some((p) => p.id === prop.id);
      if (!exists) return [prop, ...prev];
      return prev.map((p) => (p.id === prop.id ? prop : p));
    });
    return { prop, diff };
  }, []);

  const refreshAll = useCallback(
    async (targetAgentId?: string) => {
      const aid = targetAgentId ?? options?.agentId;
      setLoading(true);
      setError(null);
      try {
        const [sessList, propList] = await Promise.all([
          listConductorSessions(aid ? { agent_id: aid } : undefined),
          listConductorProposals(aid ? { agent_id: aid } : undefined),
        ]);
        setSessions(sessList);
        setProposals(propList);
        if (sessList.length > 0 && !activeSession) {
          setActiveSession(sessList[0]);
        }
        if (propList.length > 0 && !activeProposal) {
          await loadProposalAndDiff(propList[0].id);
        }
      } catch (err: unknown) {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to load Conductor sessions and proposals.';
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [options?.agentId, activeSession, activeProposal, loadProposalAndDiff]
  );

  useEffect(() => {
    if (options?.autoLoad !== false && options?.agentId) {
      void refreshAll(options.agentId);
    }
  }, [options?.autoLoad, options?.agentId, refreshAll]);

  const startSession = useCallback(
    async (payload: ConductorSessionCreatePayload) => {
      setBusyAction('create_session');
      setError(null);
      try {
        const created = await createConductorSession(payload);
        setActiveSession(created);
        setSessions((prev) => [created, ...prev]);
        return created;
      } catch (err: unknown) {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to create Conductor session.';
        setError(msg);
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    []
  );

  const selectSession = useCallback(async (sessionId: string) => {
    setLoading(true);
    setError(null);
    try {
      const s = await getConductorSession(sessionId);
      setActiveSession(s);
      return s;
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to load Conductor session.'
      );
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const createProposal = useCallback(
    async (payload: ConductorPromptPayload) => {
      setBusyAction('propose');
      setError(null);
      try {
        const created = await submitConductorRequest(payload);
        const diff = await getProposalDiff(created.id);
        setActiveProposal(created);
        setActiveDiff(diff);
        setProposals((prev) => [created, ...prev]);
        return created;
      } catch (err: unknown) {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to generate Conductor proposal.';
        setError(msg);
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    []
  );

  const runValidate = useCallback(
    async (proposalId: string) => {
      setBusyAction('validate');
      setError(null);
      try {
        await validateProposal(proposalId);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Proposal validation failed.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const runSimulate = useCallback(
    async (proposalId: string, payload?: ConductorSimulatePayload) => {
      setBusyAction('simulate');
      setError(null);
      try {
        await simulateProposal(proposalId, payload);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Proposal simulation failed.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleApproveChange = useCallback(
    async (proposalId: string, changeId: string, reason = '') => {
      setBusyAction(`approve_change_${changeId}`);
      setError(null);
      try {
        await approveChange(proposalId, changeId, reason);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Failed to approve change.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleRejectChange = useCallback(
    async (proposalId: string, changeId: string, reason = '') => {
      setBusyAction(`reject_change_${changeId}`);
      setError(null);
      try {
        await rejectChange(proposalId, changeId, reason);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Failed to reject change.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleUndoChange = useCallback(
    async (proposalId: string, changeId: string, reason = '') => {
      setBusyAction(`undo_change_${changeId}`);
      setError(null);
      try {
        await undoChangeApproval(proposalId, changeId, reason);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Failed to undo change review.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleApproveProposal = useCallback(
    async (
      proposalId: string,
      options?: { reason?: string; safe_only?: boolean }
    ) => {
      setBusyAction('approve_proposal');
      setError(null);
      try {
        await approveProposal(proposalId, options);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Failed to approve proposal.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleRejectProposal = useCallback(
    async (proposalId: string, reason = '') => {
      setBusyAction('reject_proposal');
      setError(null);
      try {
        await rejectProposal(proposalId, reason);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : 'Failed to reject proposal.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleUndoProposal = useCallback(
    async (proposalId: string, reason = '') => {
      setBusyAction('undo_proposal');
      setError(null);
      try {
        await undoProposalApproval(proposalId, reason);
        const { prop } = await loadProposalAndDiff(proposalId);
        return prop;
      } catch (err: unknown) {
        setError(
          err instanceof Error
            ? err.message
            : 'Failed to undo proposal approvals.'
        );
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  const handleApplyProposal = useCallback(
    async (proposalId: string, payload?: ConductorApplyPayload) => {
      setBusyAction('apply_proposal');
      setError(null);
      try {
        const res = await applyProposal(proposalId, payload);
        setApplyResult(res);
        await loadProposalAndDiff(proposalId);
        return res;
      } catch (err: unknown) {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to apply approved proposal.';
        setError(msg);
        // Reload proposal in case backend marked it STALE
        try {
          await loadProposalAndDiff(proposalId);
        } catch {
          // ignore
        }
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [loadProposalAndDiff]
  );

  return {
    sessions,
    activeSession,
    proposals,
    activeProposal,
    activeDiff,
    applyResult,
    loading,
    busyAction,
    error,
    refreshAll,
    startSession,
    selectSession,
    loadProposalAndDiff,
    createProposal,
    runValidate,
    runSimulate,
    handleApproveChange,
    handleRejectChange,
    handleUndoChange,
    handleApproveProposal,
    handleRejectProposal,
    handleUndoProposal,
    handleApplyProposal,
  };
}
