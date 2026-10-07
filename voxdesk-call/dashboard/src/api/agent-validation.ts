/**
 * dashboard/src/api/agent-validation.ts
 *
 * Agent configuration validation client.
 *
 * Served by `POST /api/v1/agents/{agent_id}/validate`
 * (`app/api/agent_builder_routes.py`), which validates the draft configuration
 * across `identity`, `voice`, `model`, `knowledge_bases`, `tools`,
 * `call_handling` and `security` and returns typed issues.
 *
 * The previous version of this file called `/api/agents/{id}/agent-validation`
 * and `/api/agent-validation`, neither of which exists, and returned `null` on
 * failure — so a validation *error* and a validation *endpoint outage* looked
 * identical to the UI. These wrappers let the failure surface.
 */

import { apiClient } from './client';

export interface ValidationIssue {
  field: string;
  code: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  checked_at: string;
}

function asIssues(value: unknown): ValidationIssue[] {
  return Array.isArray(value) ? (value as ValidationIssue[]) : [];
}

export async function validateAgentConfig(agentId: string): Promise<ValidationResult> {
  const res = await apiClient.post<Partial<ValidationResult>>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/validate`,
    {},
  );
  return {
    valid: Boolean(res?.valid),
    errors: asIssues(res?.errors),
    warnings: asIssues(res?.warnings),
    checked_at: res?.checked_at ?? new Date().toISOString(),
  };
}

export async function getAgentValidation(agentId: string): Promise<ValidationResult> {
  return validateAgentConfig(agentId);
}
