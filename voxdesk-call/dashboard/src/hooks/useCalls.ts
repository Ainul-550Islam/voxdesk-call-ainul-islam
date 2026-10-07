import { useCallback, useEffect, useState } from 'react';
import {
  getCallTestReadiness,
  initiatePhoneCallTest,
  listCallTestRuns,
  sendWebCallEvent,
  startWebCallSession,
} from '../api/calls';
import type {
  CallTestReadiness,
  PhoneCallTestPayload,
  TestRun,
  WebCallSessionEventPayload,
  WebCallSessionPayload,
} from '../types/evaluation';

export function useCalls() {
  const [readiness, setReadiness] = useState<CallTestReadiness | null>(null);
  const [callRuns, setCallRuns] = useState<TestRun[]>([]);
  const [activeWebCall, setActiveWebCall] = useState<TestRun | null>(null);
  const [lastPhoneCall, setLastPhoneCall] = useState<TestRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadReadiness = useCallback(async () => {
    try {
      const info = await getCallTestReadiness();
      setReadiness(info);
      return info;
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to load call test readiness';
      setError(msg);
      return null;
    }
  }, []);

  const loadCallRuns = useCallback(
    async (params?: { mode?: 'web_call' | 'phone_call'; agent_id?: string }) => {
      setLoading(true);
      setError(null);
      try {
        const items = await listCallTestRuns(params);
        setCallRuns(items);
        return items;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load call runs';
        setError(msg);
        return [];
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    void loadReadiness();
    void loadCallRuns();
  }, [loadReadiness, loadCallRuns]);

  const startWebCall = useCallback(async (payload: WebCallSessionPayload) => {
    setBusy(true);
    setError(null);
    try {
      const run = await startWebCallSession(payload);
      setActiveWebCall(run);
      setCallRuns((prev) => [run, ...prev]);
      return run;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to start web call';
      setError(msg);
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  const sendEventToWebCall = useCallback(
    async (runId: string, payload: WebCallSessionEventPayload) => {
      setBusy(true);
      setError(null);
      try {
        const updated = await sendWebCallEvent(runId, payload);
        setActiveWebCall(updated);
        setCallRuns((prev) => prev.map((r) => (r.id === runId ? updated : r)));
        return updated;
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : 'Failed to send web call event';
        setError(msg);
        return null;
      } finally {
        setBusy(false);
      }
    },
    []
  );

  const runPhoneCallTest = useCallback(async (payload: PhoneCallTestPayload) => {
    setBusy(true);
    setError(null);
    try {
      const run = await initiatePhoneCallTest(payload);
      setLastPhoneCall(run);
      setCallRuns((prev) => [run, ...prev]);
      return run;
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to initiate phone call test';
      setError(msg);
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  return {
    readiness,
    callRuns,
    activeWebCall,
    setActiveWebCall,
    lastPhoneCall,
    setLastPhoneCall,
    loading,
    busy,
    error,
    loadReadiness,
    loadCallRuns,
    startWebCall,
    sendEventToWebCall,
    runPhoneCallTest,
  };
}
