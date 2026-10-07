/**
 * dashboard/src/hooks/useAgentModelCatalog.ts
 *
 * LLM model catalog for the Agent Studio model screen.
 *
 * Backed by `GET /api/agents/models` (`app/api/agent_catalog_routes.py`) via
 * `api/agent-models.ts`. The previous version of this hook was a placeholder:
 * it set `data = { agentId }` and never issued a request, so the model picker
 * had nothing to render and no way to report why.
 *
 * Honesty fields from the backend are surfaced unchanged:
 *
 * - `selectablePresets` / `selectableProviders` — what the UI may offer.
 * - `defaultProvider` / `defaultPreset` are **null** when nothing in this
 *   deployment is selectable. The UI must render "not configured" then, not
 *   fall back to a guessed model.
 * - Model names come from `llm_factory.PRESETS`, so the picker can never show
 *   a model the runtime does not build.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getModelProviders,
  type ModelPreset,
  type ModelProviderState,
  type ModelsCatalog,
} from '../api/agent-models';

export interface UseAgentModelCatalogResult {
  catalog: ModelsCatalog | null;
  providers: ModelProviderState[];
  selectableProviders: ModelProviderState[];
  unavailableProviders: ModelProviderState[];
  presets: ModelPreset[];
  selectablePresets: ModelPreset[];
  /** Null when this deployment has no selectable preset. Never a guess. */
  defaultProvider: string | null;
  defaultPreset: string | null;
  defaultProviderSource: string;
  configuredSttModel: string;
  configuredTtsModel: string;
  notes: string[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useAgentModelCatalog(): UseAgentModelCatalogResult {
  const [catalog, setCatalog] = useState<ModelsCatalog | null>(null);
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
      const next = await getModelProviders();
      if (!mounted.current) return;
      setCatalog(next);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load model catalog');
      setCatalog(null);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const providers = catalog?.providers ?? [];
  const presets = catalog?.presets ?? [];

  return {
    catalog,
    providers,
    selectableProviders: providers.filter((provider) => provider.selectable),
    unavailableProviders: providers.filter((provider) => !provider.selectable),
    presets,
    selectablePresets: presets.filter((preset) => preset.selectable),
    defaultProvider: catalog?.default_provider ?? null,
    defaultPreset: catalog?.default_preset ?? null,
    defaultProviderSource: catalog?.default_provider_source ?? 'first_selectable_preset',
    configuredSttModel: catalog?.configured_stt_model ?? '',
    configuredTtsModel: catalog?.configured_tts_model ?? '',
    notes: catalog?.notes ?? [],
    loading,
    error,
    reload,
  };
}

export default useAgentModelCatalog;
