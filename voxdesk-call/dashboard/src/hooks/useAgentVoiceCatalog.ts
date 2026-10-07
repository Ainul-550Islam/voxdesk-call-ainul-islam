/**
 * dashboard/src/hooks/useAgentVoiceCatalog.ts
 *
 * TTS voice catalog for the Agent Studio voice screen.
 *
 * Backed by `GET /api/agents/voices` (`app/api/agent_catalog_routes.py`) via
 * `api/agent-voices.ts`. The previous version of this hook was a placeholder:
 * it set `data = { agentId }` and never issued a request, so the voice picker
 * had nothing to render and no way to report why.
 *
 * The hook deliberately exposes the honesty fields from the backend unchanged:
 *
 * - `selectableProviders` — the only providers the UI should offer. It is
 *   `configured && installed && contract_declared`, never "everything allowed".
 * - `unavailableProviders` — the rest, each with the server's `reason`, so the
 *   screen can say *why* a provider is greyed out instead of hiding it.
 * - `configuredVoices` — only the voice IDs this deployment configured. A
 *   provider's full library is never fabricated; `voiceLibraryFetched` is
 *   always false.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getVoiceProviders,
  type CatalogVoice,
  type VoiceProviderState,
  type VoicesCatalog,
} from '../api/agent-voices';

export interface UseAgentVoiceCatalogResult {
  catalog: VoicesCatalog | null;
  providers: VoiceProviderState[];
  selectableProviders: VoiceProviderState[];
  unavailableProviders: VoiceProviderState[];
  configuredVoices: CatalogVoice[];
  defaultProvider: string;
  defaultVoiceId: string;
  voiceLibraryFetched: boolean;
  voiceLibraryNote: string;
  notes: string[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useAgentVoiceCatalog(): UseAgentVoiceCatalogResult {
  const [catalog, setCatalog] = useState<VoicesCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
      const next = await getVoiceProviders();
      if (!mounted.current) return;
      setCatalog(next);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load voice catalog');
      setCatalog(null);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const providers = catalog?.providers ?? [];

  return {
    catalog,
    providers,
    selectableProviders: providers.filter((provider) => provider.selectable),
    unavailableProviders: providers.filter((provider) => !provider.selectable),
    configuredVoices: catalog?.voices ?? [],
    defaultProvider: catalog?.default_provider ?? '',
    defaultVoiceId: catalog?.default_voice_id ?? '',
    voiceLibraryFetched: Boolean(catalog?.voice_library_fetched),
    voiceLibraryNote: catalog?.voice_library_note ?? '',
    notes: catalog?.notes ?? [],
    loading,
    error,
    reload,
  };
}

export default useAgentVoiceCatalog;
