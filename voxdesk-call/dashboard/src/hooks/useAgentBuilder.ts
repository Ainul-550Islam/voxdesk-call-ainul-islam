import { useEffect, useState, useCallback, useRef } from 'react';
import {
  getBuilderConfig,
  updateBuilderConfig,
  validateAgent,
  publishAgent,
} from '../api/agent-builder';
import type {
  BuilderConfig,
  ValidationResult,
  SaveState,
} from '../types/agent-builder';

export function useAgentBuilder(agentId: string) {
  const [config, setConfig] = useState<BuilderConfig | null>(null);
  const [localConfig, setLocalConfig] = useState<BuilderConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>('SAVED');
  const [lastSaved, setLastSaved] = useState<string | undefined>();
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const etagRef = useRef<string | undefined>();

  const load = useCallback(async () => {
    if (!agentId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const c = await getBuilderConfig(agentId);
      setConfig(c);
      setLocalConfig(c);
      etagRef.current = c.etag;
      setSaveState('SAVED');
    } catch (e: any) {
      setError(e?.message || 'Failed to load agent builder configuration');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const isDirty = JSON.stringify(config) !== JSON.stringify(localConfig);

  useEffect(() => {
    if (isDirty && saveState !== 'CONFLICT') {
      setSaveState('UNSAVED');
    }
  }, [isDirty, saveState]);

  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  const save = useCallback(async () => {
    if (!localConfig || !agentId) return;
    setSaveState('SAVING');
    setError(null);
    try {
      const updated = await updateBuilderConfig(
        agentId,
        localConfig,
        etagRef.current
      );
      setConfig(updated);
      setLocalConfig(updated);
      etagRef.current = updated.etag;
      setSaveState('SAVED');
      setLastSaved(new Date().toISOString());
    } catch (e: any) {
      if (e?.status === 409) {
        setSaveState('CONFLICT');
        setError(
          'Conflict (409): Another session modified this draft. Reload the latest configuration before saving.'
        );
      } else {
        setSaveState('ERROR');
        setError(e?.message || 'Save failed');
      }
    }
  }, [agentId, localConfig]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        save();
      }
    },
    [save]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown as any);
    return () => window.removeEventListener('keydown', handleKeyDown as any);
  }, [handleKeyDown]);

  const doValidate = useCallback(async () => {
    try {
      const v = await validateAgent(agentId);
      setValidation(v);
      return v;
    } catch (e: any) {
      const v = {
        valid: false,
        errors: [
          {
            field: 'general',
            message: e?.message || 'Validation failed',
            severity: 'error' as const,
          },
        ],
        warnings: [],
      };
      setValidation(v);
      return v;
    }
  }, [agentId]);

  const doPublish = useCallback(
    async (releaseNotes = '') => {
      const v = await doValidate();
      if (!v.valid) return { success: false, errors: v.errors };
      try {
        const r = await publishAgent(agentId, { release_notes: releaseNotes });
        await load();
        return { ...r, success: true };
      } catch (e: any) {
        return {
          success: false,
          errors: [
            {
              field: 'general',
              message: e?.message || 'Publish failed',
              severity: 'error' as const,
            },
          ],
        };
      }
    },
    [agentId, doValidate, load]
  );

  return {
    config,
    localConfig,
    setLocalConfig,
    loading,
    error,
    saveState,
    lastSaved,
    isDirty,
    save,
    load,
    validation,
    doValidate,
    doPublish,
  };
}
