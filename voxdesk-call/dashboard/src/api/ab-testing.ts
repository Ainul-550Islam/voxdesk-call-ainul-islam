import { client } from './client';

export interface ExperimentVariantInput {
  name: string;
  agent_version_id?: string | null;
  weight?: number;
  is_control?: boolean;
  config?: Record<string, unknown>;
  config_overrides?: Record<string, unknown>;
  prompt?: string;
}

export interface ExperimentVariant {
  id: string;
  name: string;
  agent_version_id: string | null;
  weight: number;
  is_control: boolean;
  config_overrides: Record<string, unknown>;
  impressions: number;
  conversions: number;
  conversion_rate: number;
}

export interface Experiment {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  agent_id: string | null;
  status: 'draft' | 'running' | 'paused' | 'completed' | 'promoted' | 'rolled_back' | string;
  traffic_split: Record<string, unknown>;
  success_metric: string;
  min_sample_size: number;
  confidence_level: number;
  winner_variant_id: string | null;
  variants: ExperimentVariant[];
  started_at: string | null;
  ended_at: string | null;
  promoted_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ExperimentListResponse {
  experiments: Experiment[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExperimentCreateInput {
  name: string;
  description?: string | null;
  agent_id?: string | null;
  success_metric?: string;
  min_sample_size?: number;
  confidence_level?: number;
  variants: ExperimentVariantInput[];
}

export interface ExperimentUpdateInput {
  name?: string;
  description?: string | null;
  min_sample_size?: number;
  confidence_level?: number;
  traffic_weights?: Record<string, number>;
}

export interface VariantResultStat {
  variant_id: string;
  name: string;
  is_control: boolean;
  impressions: number;
  conversions: number;
  conversion_rate: number;
  ci_lower: number;
  ci_upper: number;
  z_score: number | null;
  p_value: number | null;
  significant: boolean | null;
  lift_pct: number | null;
}

export interface ExperimentResultsResponse {
  experiment_id: string;
  status: string;
  total_impressions: number;
  min_sample_size: number;
  sample_size_reached: boolean;
  confidence_level: number;
  variants: VariantResultStat[];
  recommended_winner_variant_id: string | null;
}

export interface PromoteExperimentResponse {
  experiment_id: string;
  status: string;
  winner_variant_id: string;
  agent_id: string;
  previous_version_id: string | null;
  previous_version_number: number | null;
  promoted_version_id: string;
  promoted_version_number: number;
  promoted_at: string;
}

export interface RollbackExperimentResponse {
  experiment_id: string;
  status: string;
  agent_id: string;
  rolled_back_from_version_id: string | null;
  restored_version_id: string | null;
  restored_version_number: number | null;
  rolled_back_at: string;
}

export async function listExperiments(status?: string): Promise<ExperimentListResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  return client.get<ExperimentListResponse>(`/api/ab-testing/experiments${qs}`);
}

export async function getExperiment(experimentId: string): Promise<Experiment> {
  return client.get<Experiment>(`/api/ab-testing/experiments/${encodeURIComponent(experimentId)}`);
}

export async function createExperiment(payload: ExperimentCreateInput): Promise<Experiment> {
  const body = {
    agent_id: payload.agent_id || '',
    name: payload.name,
    description: payload.description || '',
    variants: payload.variants.map((v) => ({
      name: v.name,
      weight: v.weight ?? 50,
      config: v.config ?? v.config_overrides ?? {},
      prompt: v.prompt ?? '',
      is_control: Boolean(v.is_control),
    })),
  };
  return client.post<Experiment>('/api/ab-testing/experiments', body);
}

export async function updateExperiment(
  experimentId: string,
  payload: ExperimentUpdateInput,
): Promise<Experiment> {
  return client.patch<Experiment>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}`,
    payload,
  );
}

export async function deleteExperiment(
  experimentId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.delete<{ id: string; deleted: boolean }>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}`,
  );
}

export async function startExperiment(experimentId: string): Promise<Experiment> {
  return client.post<Experiment>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}/start`,
  );
}

export async function pauseExperiment(experimentId: string): Promise<Experiment> {
  return client.post<Experiment>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}/pause`,
  );
}

export async function completeExperiment(experimentId: string): Promise<Experiment> {
  return client.post<Experiment>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}/complete`,
  );
}

export async function getExperimentResults(
  experimentId: string,
): Promise<ExperimentResultsResponse> {
  const raw = await client.get<Record<string, unknown>>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}/results`,
  );
  if (typeof raw.sample_size_reached === 'boolean') {
    return raw as unknown as ExperimentResultsResponse;
  }
  const comparisons = Array.isArray(raw.comparisons)
    ? (raw.comparisons as Array<Record<string, unknown>>)
    : [];
  const compByTreatment = new Map<string, Record<string, unknown>>();
  for (const c of comparisons) {
    if (typeof c.treatment_variant_id === 'string') {
      compByTreatment.set(c.treatment_variant_id, c);
    }
  }
  const minSample = Number(raw.min_samples_per_arm ?? raw.min_sample_size ?? 30);
  const rawVariants = Array.isArray(raw.variants)
    ? (raw.variants as Array<Record<string, unknown>>)
    : [];
  const variants: VariantResultStat[] = rawVariants.map((v) => {
    const vid = String(v.variant_id ?? v.id ?? '');
    const comp = compByTreatment.get(vid);
    const zTest = (comp?.success_z_test as Record<string, unknown> | undefined) ?? undefined;
    const impressions = Number(v.impressions ?? v.n ?? 0);
    const conversions = Number(v.conversions ?? v.successes ?? 0);
    const rate = Number(v.conversion_rate ?? (impressions > 0 ? conversions / impressions : 0));
    return {
      variant_id: vid,
      name: String(v.name ?? ''),
      is_control: Boolean(v.is_control),
      impressions,
      conversions,
      conversion_rate: rate,
      ci_lower: Math.max(0, rate - 0.05),
      ci_upper: Math.min(1, rate + 0.05),
      z_score: zTest && typeof zTest.z_stat === 'number' ? zTest.z_stat : null,
      p_value: zTest && typeof zTest.p_value === 'number' ? zTest.p_value : null,
      significant: comp ? Boolean(comp.statistically_significant) : null,
      lift_pct: zTest && typeof zTest.lift === 'number' ? zTest.lift * 100 : null,
    };
  });
  return {
    experiment_id: String(raw.experiment_id ?? experimentId),
    status: String(raw.status ?? 'insufficient_data'),
    total_impressions: Number(raw.total_impressions ?? raw.total_calls ?? 0),
    min_sample_size: minSample,
    sample_size_reached: rawVariants.length >= 2 && variants.every((v) => v.impressions >= minSample),
    confidence_level: 0.95,
    variants,
    recommended_winner_variant_id:
      typeof raw.recommended_winner_variant_id === 'string'
        ? raw.recommended_winner_variant_id
        : null,
  };
}

export async function promoteExperiment(
  experimentId: string,
  variantId?: string,
): Promise<PromoteExperimentResponse> {
  return client.post<PromoteExperimentResponse>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}/promote`,
    variantId ? { variant_id: variantId } : {},
  );
}

export async function rollbackExperiment(
  experimentId: string,
): Promise<RollbackExperimentResponse> {
  return client.post<RollbackExperimentResponse>(
    `/api/ab-testing/experiments/${encodeURIComponent(experimentId)}/rollback`,
    {},
  );
}
