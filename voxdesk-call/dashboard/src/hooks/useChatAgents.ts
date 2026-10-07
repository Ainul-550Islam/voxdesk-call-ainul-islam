import { useCallback, useEffect, useState } from 'react';
import {
  archiveChatAgent,
  createChatAgent,
  createChatSession,
  endChatSession,
  listChatAgents,
  listChatAgentVersions,
  listChatMessages,
  publishChatAgent,
  restoreChatAgent,
  rollbackChatAgentVersion,
  sendChatMessage,
  updateChatAgentDraft,
  validateChatAgentDraft,
} from '../api/chat-agents';
import type {
  ChatAgentConfig,
  ChatAgentRecord,
  ChatAgentValidationResponse,
  ChatAgentVersionRecord,
  ChatMessageRecord,
  ChatSessionRecord,
} from '../api/types/chat-agent';

export function useChatAgents() {
  const [agents, setAgents] = useState<ChatAgentRecord[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<ChatAgentRecord | null>(null);
  const [versions, setVersions] = useState<ChatAgentVersionRecord[]>([]);
  const [validation, setValidation] = useState<ChatAgentValidationResponse | null>(
    null
  );
  const [activeSession, setActiveSession] = useState<ChatSessionRecord | null>(
    null
  );
  const [messages, setMessages] = useState<ChatMessageRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listChatAgents();
      setAgents(list);
      if (selectedAgent) {
        const refreshed = list.find((a) => a.id === selectedAgent.id) || null;
        setSelectedAgent(refreshed);
      } else if (list.length > 0) {
        setSelectedAgent(list[0]);
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to load chat agents');
    } finally {
      setLoading(false);
    }
  }, [selectedAgent]);

  useEffect(() => {
    reload();
  }, []);

  const loadVersions = useCallback(async (agentId: string) => {
    try {
      const v = await listChatAgentVersions(agentId);
      setVersions(v);
      return v;
    } catch {
      setVersions([]);
      return [];
    }
  }, []);

  useEffect(() => {
    if (selectedAgent?.id) {
      loadVersions(selectedAgent.id);
    } else {
      setVersions([]);
    }
  }, [selectedAgent?.id, loadVersions]);

  const create = useCallback(
    async (name: string, description = '', draftConfig: ChatAgentConfig = {}) => {
      const created = await createChatAgent({
        name,
        description,
        draft_config: draftConfig,
      });
      await reload();
      setSelectedAgent(created);
      return created;
    },
    [reload]
  );

  const saveDraft = useCallback(
    async (
      agentId: string,
      patch: {
        name?: string;
        description?: string;
        draft_config?: ChatAgentConfig;
      },
      etag?: string
    ) => {
      setConflictError(null);
      setError(null);
      try {
        const updated = await updateChatAgentDraft(agentId, patch, etag);
        setSelectedAgent(updated);
        setAgents((prev) =>
          prev.map((a) => (a.id === updated.id ? updated : a))
        );
        return updated;
      } catch (e: any) {
        if (e?.status === 409) {
          setConflictError(
            'Conflict (409): Another session modified this chat agent draft. Reload before saving.'
          );
        } else {
          setError(e?.message || 'Failed to save chat agent draft');
        }
        throw e;
      }
    },
    []
  );

  const validate = useCallback(async (agentId: string) => {
    const res = await validateChatAgentDraft(agentId);
    setValidation(res);
    return res;
  }, []);

  const publish = useCallback(
    async (agentId: string, changeSummary = '') => {
      const ver = await publishChatAgent(agentId, changeSummary);
      await reload();
      await loadVersions(agentId);
      return ver;
    },
    [reload, loadVersions]
  );

  const rollback = useCallback(
    async (agentId: string, targetVersion: number, reason = '') => {
      const ver = await rollbackChatAgentVersion(agentId, targetVersion, reason);
      await reload();
      await loadVersions(agentId);
      return ver;
    },
    [reload, loadVersions]
  );

  const archive = useCallback(
    async (agentId: string) => {
      const res = await archiveChatAgent(agentId);
      await reload();
      return res;
    },
    [reload]
  );

  const restore = useCallback(
    async (agentId: string) => {
      const res = await restoreChatAgent(agentId);
      await reload();
      return res;
    },
    [reload]
  );

  const startSession = useCallback(
    async (
      agentId: string,
      options?: {
        contact_phone?: string;
        contact_name?: string;
        channel?: 'web' | 'sms' | 'whatsapp' | 'api';
        dynamic_variables?: Record<string, unknown>;
      }
    ) => {
      const sess = await createChatSession(agentId, options);
      setActiveSession(sess);
      const msgs = await listChatMessages(sess.id);
      setMessages(msgs);
      return sess;
    },
    []
  );

  const sendMessage = useCallback(
    async (
      sessionId: string,
      content: string,
      memoryUpdates?: Record<string, string>
    ) => {
      const turn = await sendChatMessage(sessionId, {
        content,
        memory_updates: memoryUpdates,
      });
      setActiveSession(turn.session);
      const msgs = await listChatMessages(sessionId);
      setMessages(msgs);
      return turn;
    },
    []
  );

  const closeSession = useCallback(async (sessionId: string) => {
    const ended = await endChatSession(sessionId, 'completed');
    setActiveSession(ended);
    return ended;
  }, []);

  return {
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
    loadVersions,
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
  };
}
