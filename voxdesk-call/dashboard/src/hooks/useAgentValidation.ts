/**
 * dashboard/src/hooks/useAgentValidation.ts
 *
 * Agent configuration validation.
 *
 * Backed by `POST /api/v1/agents/{agent_id}/validate`
 * (`app/api/agent_builder_routes.py`) via `api/agent-validation.ts`. The
 * previous version of this hook was a placeholder: it set
 * `data = { agentId }` and never issued a request, so "valid" and "never
 * checked" were indistinguishable in the UI.
 *
 * The distinction is preserved here: `validated` means the server actually
 * answered. `valid === false` with `validated === true` is a real
 * configuration error; `validated === false` means there is no result yet.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  validateAgentConfig,
  type ValidationIssue,
  type ValidationResult,
} from '../api/agent-validation';

export interface UseAgentValidationResult {
  result: ValidationResult | null;
  /** True only after the server has answered at least once. */
  validated: boolean;
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  /** Issues keyed by field, for inline form errors. */
  errorsByField: Record<string, ValidationIssue[]>;
  loading: boolean;
  error: string | null;
  validate: () => Promise<ValidationResult | null>;
  reset: () => void;
}

function groupByField(issues: ValidationIssue[]): Record<string, ValidationIssue[]> {
  return issues.reduce<Record<string, ValidationIssue[]>>((accumulator, issue) => {
    const key = issue.field || '_';
    accumulator[key] = accumulator[key] ? [...accumulator[key], issue] : [issue];
    return accumulator;
  }, {});
}

export function useAgentValidation(agentId?: string): UseAgentValidationResult {
  const [result, setResult] = useState<ValidationResult | null>(null);
  const [validated, setValidated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const validate = useCallback(async () => {
    if (!agentId) {
      setError('No agent selected');
      return null;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await validateAgentConfig(agentId);
      if (!mounted.current) return next;
      setResult(next);
      setValidated(true);
      return next;
    } catch (err) {
      if (!mounted.current) return null;
      setError(err instanceof Error ? err.message : 'Validation failed');
      // A failed request is not a failed validation: keep any previous result
      // and do not claim a verdict we did not receive.
      return null;
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [agentId]);

  const reset = useCallback(() => {
    setResult(null);
    setValidated(false);
    setError(null);
  }, []);

  // Changing agent invalidates the previous verdict rather than showing stale
  // results against a different configuration.
  useEffect(() => {
    reset();
  }, [agentId, reset]);

  const errors = result?.errors ?? [];
  const warnings = result?.warnings ?? [];

  return {
    result,
    validated,
    valid: validated ? Boolean(result?.valid) : false,
    errors,
    warnings,
    errorsByField: groupByField(errors),
    loading,
    error,
    validate,
    reset,
  };
}

export default useAgentValidation;
