import React, { useState } from 'react';
import type {
  EvaluationRule,
  EvaluationRuleCreatePayload,
  EvaluationRuleType,
  EvaluationRuleUpdatePayload,
} from '../../types/evaluation';

const RULE_TYPE_OPTIONS: Array<{
  value: EvaluationRuleType;
  label: string;
  hint: string;
}> = [
  {
    value: 'contains',
    label: 'Contains Substring',
    hint: 'Assert that assistant reply contains a required phrase.',
  },
  {
    value: 'not_contains',
    label: 'Does Not Contain',
    hint: 'Assert that forbidden phrase never appears in assistant reply.',
  },
  {
    value: 'regex',
    label: 'Bounded Regex Match',
    hint: 'Assert that assistant reply matches a safe regular expression.',
  },
  {
    value: 'json_path_exists',
    label: 'JSONPath Exists',
    hint: 'Assert that a bounded JSONPath (e.g. $.variables.booking_status) is present.',
  },
  {
    value: 'json_path_equals',
    label: 'JSONPath Equals',
    hint: 'Assert that a bounded JSONPath equals an expected value.',
  },
  {
    value: 'tool_called',
    label: 'Tool Called',
    hint: 'Assert that a specific tool (e.g. book_appointment) was invoked.',
  },
  {
    value: 'tool_not_called',
    label: 'Tool Not Called',
    hint: 'Assert that a specific tool was not invoked during the run.',
  },
  {
    value: 'transfer_occurred',
    label: 'Transfer Occurred',
    hint: 'Assert whether a human/agent call transfer was triggered.',
  },
  {
    value: 'variable_equals',
    label: 'Runtime Variable Equals',
    hint: 'Assert that a dynamic/extracted runtime variable equals expected value.',
  },
  {
    value: 'turn_count_max',
    label: 'Max Turn Count',
    hint: 'Assert that total conversation turns do not exceed threshold.',
  },
  {
    value: 'turn_count_min',
    label: 'Min Turn Count',
    hint: 'Assert that conversation has at least threshold turns.',
  },
  {
    value: 'latency_ms_max',
    label: 'Max Latency (ms)',
    hint: 'Assert that P95 / max turn latency stays within SLA budget.',
  },
  {
    value: 'final_state_equals',
    label: 'Final State Equals',
    hint: 'Assert that final conversation state matches (e.g. completed, transferred).',
  },
  {
    value: 'llm_judge',
    label: 'LLM-as-Judge Rubric',
    hint: 'Evaluate conversation against a natural-language rubric (labeled with judge model).',
  },
];

export interface EvaluationRulesEditorProps {
  rules: EvaluationRule[];
  suiteId?: string | null;
  testCaseId?: string | null;
  loading?: boolean;
  onCreateRule: (payload: EvaluationRuleCreatePayload) => Promise<unknown>;
  onUpdateRule: (
    ruleId: string,
    payload: EvaluationRuleUpdatePayload
  ) => Promise<unknown>;
  onDeleteRule: (ruleId: string) => Promise<unknown>;
}

export const EvaluationRulesEditor: React.FC<EvaluationRulesEditorProps> = ({
  rules,
  suiteId,
  testCaseId,
  loading = false,
  onCreateRule,
  onUpdateRule,
  onDeleteRule,
}) => {
  const [name, setName] = useState('');
  const [ruleType, setRuleType] = useState<EvaluationRuleType>('contains');
  const [weight, setWeight] = useState<number>(1.0);
  const [primaryValue, setPrimaryValue] = useState('');
  const [secondaryValue, setSecondaryValue] = useState('');
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const buildConfig = (): Record<string, unknown> => {
    switch (ruleType) {
      case 'contains':
      case 'not_contains':
        return {
          substring: primaryValue.trim(),
          case_sensitive: caseSensitive,
          target: 'assistant_transcript',
        };
      case 'regex':
        return {
          pattern: primaryValue.trim(),
          case_sensitive: caseSensitive,
          target: 'assistant_transcript',
        };
      case 'json_path_exists':
        return { path: primaryValue.trim() };
      case 'json_path_equals':
        return { path: primaryValue.trim(), expected: secondaryValue.trim() };
      case 'tool_called':
      case 'tool_not_called':
        return { tool_name: primaryValue.trim() };
      case 'transfer_occurred':
        return {
          expected: primaryValue.trim().toLowerCase() !== 'false',
          destination: secondaryValue.trim() || undefined,
        };
      case 'variable_equals':
        return {
          variable_name: primaryValue.trim(),
          expected: secondaryValue.trim(),
        };
      case 'turn_count_max':
      case 'turn_count_min':
        return { threshold: Number(primaryValue || 6) };
      case 'latency_ms_max':
        return { max_ms: Number(primaryValue || 1500) };
      case 'final_state_equals':
        return { expected_state: primaryValue.trim() || 'completed' };
      case 'llm_judge':
        return {
          rubric: primaryValue.trim(),
          pass_threshold: Number(secondaryValue || 0.7),
        };
      default:
        return {};
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!name.trim()) {
      setFormError('Rule name is required.');
      return;
    }
    if (
      ruleType !== 'transfer_occurred' &&
      !primaryValue.trim()
    ) {
      setFormError('Rule target/assertion value is required.');
      return;
    }
    setSubmitting(true);
    try {
      await onCreateRule({
        suite_id: suiteId || undefined,
        test_case_id: testCaseId || undefined,
        name: name.trim(),
        rule_type: ruleType,
        config: buildConfig(),
        weight: Number(weight) || 1.0,
        enabled: true,
      });
      setName('');
      setPrimaryValue('');
      setSecondaryValue('');
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : 'Failed to create evaluation rule'
      );
    } finally {
      setSubmitting(false);
    }
  };

  const renderPrimaryPlaceholder = (): string => {
    switch (ruleType) {
      case 'contains':
      case 'not_contains':
        return 'Substring (e.g. confirmed your appointment)';
      case 'regex':
        return 'Regex pattern (e.g. APT-\\d{4})';
      case 'json_path_exists':
      case 'json_path_equals':
        return 'JSONPath (e.g. $.variables.booking_status)';
      case 'tool_called':
      case 'tool_not_called':
        return 'Tool name (e.g. book_appointment)';
      case 'transfer_occurred':
        return 'Expected transfer? (true / false)';
      case 'variable_equals':
        return 'Variable name (e.g. booking_status)';
      case 'turn_count_max':
      case 'turn_count_min':
        return 'Turn count threshold (e.g. 6)';
      case 'latency_ms_max':
        return 'Max latency in ms (e.g. 1200)';
      case 'final_state_equals':
        return 'Expected final state (e.g. completed or transferred)';
      case 'llm_judge':
        return 'Judge rubric (e.g. Agent politely confirms date and time)';
      default:
        return 'Value';
    }
  };

  const needsSecondaryField =
    ruleType === 'json_path_equals' ||
    ruleType === 'variable_equals' ||
    ruleType === 'transfer_occurred' ||
    ruleType === 'llm_judge';

  const enabledRulesCount = rules.filter((r) => r.enabled).length;

  return (
    <div
      data-testid="evaluation-rules-editor"
      style={{
        background: 'rgba(15, 23, 42, 0.75)',
        border: '1px solid rgba(148, 163, 184, 0.2)',
        borderRadius: 12,
        padding: 20,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 14,
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#f8fafc' }}>
            Evaluation Rules & Assertions
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>
            Deterministic & LLM-as-judge rules evaluated against persisted TestRun evidence.
          </p>
        </div>
        <span
          data-testid="enabled-rules-badge"
          style={{
            padding: '4px 10px',
            borderRadius: 999,
            fontSize: 12,
            fontWeight: 600,
            background:
              enabledRulesCount > 0
                ? 'rgba(16, 185, 129, 0.16)'
                : 'rgba(245, 158, 11, 0.18)',
            color: enabledRulesCount > 0 ? '#34d399' : '#fbbf24',
          }}
        >
          {enabledRulesCount > 0
            ? `${enabledRulesCount} Active Assertion(s)`
            : '0 Active Rules (Scorecard = NO_ASSERTIONS)'}
        </span>
      </div>

      <form
        onSubmit={handleSubmit}
        data-testid="create-eval-rule-form"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: 10,
          marginBottom: 18,
          padding: 14,
          borderRadius: 10,
          background: 'rgba(30, 41, 59, 0.6)',
        }}
      >
        <input
          type="text"
          aria-label="Rule Name"
          placeholder="Rule name (e.g. Confirm Booking Tool)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid rgba(148, 163, 184, 0.3)',
            background: '#0f172a',
            color: '#f8fafc',
            fontSize: 13,
          }}
        />

        <select
          aria-label="Rule Type"
          value={ruleType}
          onChange={(e) => setRuleType(e.target.value as EvaluationRuleType)}
          style={{
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid rgba(148, 163, 184, 0.3)',
            background: '#0f172a',
            color: '#f8fafc',
            fontSize: 13,
          }}
        >
          {RULE_TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label} ({opt.value})
            </option>
          ))}
        </select>

        <input
          type="text"
          aria-label="Rule Target Value"
          placeholder={renderPrimaryPlaceholder()}
          value={primaryValue}
          onChange={(e) => setPrimaryValue(e.target.value)}
          style={{
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid rgba(148, 163, 184, 0.3)',
            background: '#0f172a',
            color: '#f8fafc',
            fontSize: 13,
          }}
        />

        {needsSecondaryField && (
          <input
            type="text"
            aria-label="Rule Expected Secondary Value"
            placeholder={
              ruleType === 'llm_judge'
                ? 'Pass threshold (0.0 - 1.0, default 0.7)'
                : ruleType === 'transfer_occurred'
                ? 'Optional transfer destination'
                : 'Expected value (e.g. confirmed)'
            }
            value={secondaryValue}
            onChange={(e) => setSecondaryValue(e.target.value)}
            style={{
              padding: '8px 10px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 13,
            }}
          />
        )}

        <input
          type="number"
          step="0.5"
          min="0.1"
          max="100"
          aria-label="Rule Weight"
          placeholder="Weight (1.0)"
          value={weight}
          onChange={(e) => setWeight(Number(e.target.value))}
          style={{
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid rgba(148, 163, 184, 0.3)',
            background: '#0f172a',
            color: '#f8fafc',
            fontSize: 13,
          }}
        />

        {(ruleType === 'contains' ||
          ruleType === 'not_contains' ||
          ruleType === 'regex') && (
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 12,
              color: '#cbd5e1',
            }}
          >
            <input
              type="checkbox"
              checked={caseSensitive}
              onChange={(e) => setCaseSensitive(e.target.checked)}
            />
            Case-sensitive
          </label>
        )}

        <button
          type="submit"
          disabled={submitting}
          style={{
            padding: '8px 14px',
            borderRadius: 8,
            border: 'none',
            background: '#3b82f6',
            color: '#ffffff',
            fontWeight: 600,
            fontSize: 13,
            cursor: submitting ? 'not-allowed' : 'pointer',
          }}
        >
          {submitting ? 'Adding...' : '+ Add Rule'}
        </button>
      </form>

      {formError && (
        <div
          role="alert"
          style={{
            marginBottom: 12,
            padding: '8px 12px',
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.16)',
            color: '#fca5a5',
            fontSize: 12,
          }}
        >
          {formError}
        </div>
      )}

      {loading ? (
        <div style={{ fontSize: 13, color: '#94a3b8' }}>Loading rules...</div>
      ) : rules.length === 0 ? (
        <div
          data-testid="empty-rules-notice"
          style={{
            padding: 16,
            borderRadius: 8,
            background: 'rgba(245, 158, 11, 0.08)',
            border: '1px dashed rgba(245, 158, 11, 0.35)',
            color: '#fcd34d',
            fontSize: 13,
          }}
        >
          No evaluation rules configured yet. Runs without enabled assertions will be
          marked <strong>NO_ASSERTIONS</strong> rather than fabricating a 100% score.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {rules.map((rule) => (
            <div
              key={rule.id}
              data-testid={`eval-rule-row-${rule.id}`}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px 14px',
                borderRadius: 8,
                background: 'rgba(15, 23, 42, 0.9)',
                border: '1px solid rgba(148, 163, 184, 0.16)',
                gap: 12,
                flexWrap: 'wrap',
              }}
            >
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <strong style={{ color: '#f8fafc', fontSize: 13 }}>
                    {rule.name}
                  </strong>
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: 6,
                      fontSize: 11,
                      background: 'rgba(59, 130, 246, 0.16)',
                      color: '#93c5fd',
                      fontFamily: 'monospace',
                    }}
                  >
                    {rule.rule_type}
                  </span>
                  <span style={{ fontSize: 11, color: '#94a3b8' }}>
                    weight: {rule.weight}
                  </span>
                </div>
                <div
                  style={{
                    marginTop: 4,
                    fontSize: 12,
                    color: '#94a3b8',
                    fontFamily: 'monospace',
                  }}
                >
                  {JSON.stringify(rule.config)}
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button
                  type="button"
                  onClick={() =>
                    void onUpdateRule(rule.id, { enabled: !rule.enabled })
                  }
                  style={{
                    padding: '4px 10px',
                    borderRadius: 6,
                    border: '1px solid rgba(148, 163, 184, 0.25)',
                    background: rule.enabled
                      ? 'rgba(16, 185, 129, 0.16)'
                      : 'rgba(100, 116, 139, 0.2)',
                    color: rule.enabled ? '#34d399' : '#94a3b8',
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  {rule.enabled ? 'Enabled' : 'Disabled'}
                </button>
                <button
                  type="button"
                  onClick={() => void onDeleteRule(rule.id)}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 6,
                    border: '1px solid rgba(239, 68, 68, 0.35)',
                    background: 'rgba(239, 68, 68, 0.12)',
                    color: '#fca5a5',
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default EvaluationRulesEditor;
