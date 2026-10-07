import React, { useState } from 'react';
import { useSimulations } from '../../hooks/useSimulations';
import { SimulationTraceViewer } from '../../features/testing/SimulationTraceViewer';
import type {
  AgentKind,
  EvaluationRuleType,
  InlineEvaluationRuleSpec,
} from '../../types/evaluation';

export const AgentPlayground: React.FC = () => {
  const {
    runs,
    selectedRun,
    setSelectedRun,
    running,
    error,
    executePlayground,
    executeSimulation,
    reevaluateRunOnly,
  } = useSimulations();

  const [modeTab, setModeTab] = useState<'llm' | 'simulation'>('llm');
  const [agentId, setAgentId] = useState('default');
  const [agentKind, setAgentKind] = useState<AgentKind>('voice');
  const [agentVersionNumber, setAgentVersionNumber] = useState<number>(1);
  const [promptOverride, setPromptOverride] = useState('');
  const [userMessage, setUserMessage] = useState(
    'Hello, I would like to book an appointment tomorrow at 10:00 AM.'
  );
  const [multiTurnScript, setMultiTurnScript] = useState(
    'Hello, I need to book an appointment tomorrow at 10:00 AM\nCan you confirm my booking code?\nThank you, goodbye'
  );
  const [customerNameVar, setCustomerNameVar] = useState('Amina Rahman');
  const [preferredTimeVar, setPreferredTimeVar] = useState('tomorrow at 10:00 AM');
  const [inlineRuleType, setInlineRuleType] =
    useState<EvaluationRuleType>('contains');
  const [inlineRuleValue, setInlineRuleValue] = useState('APT-2026');
  const [inlineRules, setInlineRules] = useState<InlineEvaluationRuleSpec[]>([
    {
      name: 'Contains Confirmation Code',
      rule_type: 'contains',
      config: { substring: 'APT-2026' },
      enabled: true,
      weight: 1.0,
    },
  ]);

  const handleAddInlineRule = () => {
    if (!inlineRuleValue.trim()) return;
    const val = inlineRuleValue.trim();
    let cfg: Record<string, unknown> = { substring: val };
    if (inlineRuleType === 'regex') cfg = { pattern: val };
    if (inlineRuleType === 'tool_called' || inlineRuleType === 'tool_not_called') {
      cfg = { tool_name: val };
    }
    if (inlineRuleType === 'final_state_equals') cfg = { expected_state: val };
    setInlineRules((prev) => [
      ...prev,
      {
        name: `${inlineRuleType}: ${val}`,
        rule_type: inlineRuleType,
        config: cfg,
        enabled: true,
        weight: 1.0,
      },
    ]);
    setInlineRuleValue('');
  };

  const handleRun = async (e: React.FormEvent) => {
    e.preventDefault();
    const dynVars: Record<string, unknown> = {};
    if (customerNameVar.trim()) dynVars.customer_name = customerNameVar.trim();
    if (preferredTimeVar.trim()) dynVars.preferred_time = preferredTimeVar.trim();

    if (modeTab === 'llm') {
      await executePlayground({
        agent_id: agentId.trim(),
        agent_kind: agentKind,
        agent_version_number: Number(agentVersionNumber) || 1,
        prompt_override: promptOverride.trim() || undefined,
        user_message: userMessage.trim(),
        dynamic_variables: dynVars,
        evaluation_rules: inlineRules,
        allow_mock_fallback: true,
      });
    } else {
      const turns = multiTurnScript
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      await executeSimulation({
        agent_id: agentId.trim(),
        agent_kind: agentKind,
        agent_version_number: Number(agentVersionNumber) || 1,
        mode: agentKind === 'chat' ? 'chat' : 'simulation',
        input_messages: turns,
        dynamic_variables: dynVars,
        evaluation_rules: inlineRules,
        allow_mock_fallback: true,
      });
    }
  };

  return (
    <div
      data-testid="agent-playground-page"
      style={{
        padding: 24,
        color: '#f8fafc',
        maxWidth: 1400,
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 20,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>
            Agent LLM & Simulation Playground
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: '#94a3b8' }}>
            Execute single-turn LLM prompts and multi-turn voice/chat simulations
            pinned to an immutable AgentVersion.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            data-testid="playground-mode-llm"
            onClick={() => setModeTab('llm')}
            style={{
              padding: '8px 14px',
              borderRadius: 8,
              border: '1px solid rgba(59, 130, 246, 0.4)',
              background:
                modeTab === 'llm' ? '#3b82f6' : 'rgba(15, 23, 42, 0.8)',
              color: '#ffffff',
              fontWeight: 600,
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            Single-Turn LLM Playground
          </button>
          <button
            type="button"
            data-testid="playground-mode-simulation"
            onClick={() => setModeTab('simulation')}
            style={{
              padding: '8px 14px',
              borderRadius: 8,
              border: '1px solid rgba(59, 130, 246, 0.4)',
              background:
                modeTab === 'simulation' ? '#3b82f6' : 'rgba(15, 23, 42, 0.8)',
              color: '#ffffff',
              fontWeight: 600,
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            Multi-Turn Conversation Simulation
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.16)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#fca5a5',
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(360px, 1fr) minmax(420px, 1.3fr)',
          gap: 20,
          alignItems: 'start',
        }}
      >
        {/* Left Configuration & Execution Form */}
        <form
          onSubmit={handleRun}
          data-testid="playground-execution-form"
          style={{
            background: 'rgba(15, 23, 42, 0.78)',
            border: '1px solid rgba(148, 163, 184, 0.2)',
            borderRadius: 12,
            padding: 20,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>
            Pinned Agent & Runtime Target
          </h3>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 120px 110px',
              gap: 10,
            }}
          >
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: '#94a3b8',
                  marginBottom: 4,
                }}
              >
                Agent ID / External Key
              </label>
              <input
                type="text"
                aria-label="Playground Agent ID"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 13,
                }}
              />
            </div>

            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: '#94a3b8',
                  marginBottom: 4,
                }}
              >
                Agent Kind
              </label>
              <select
                aria-label="Playground Agent Kind"
                value={agentKind}
                onChange={(e) => setAgentKind(e.target.value as AgentKind)}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 13,
                }}
              >
                <option value="voice">Voice Agent</option>
                <option value="chat">Chat Agent</option>
              </select>
            </div>

            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: '#94a3b8',
                  marginBottom: 4,
                }}
              >
                Pinned Version
              </label>
              <input
                type="number"
                min="1"
                aria-label="Playground Pinned Version"
                value={agentVersionNumber}
                onChange={(e) => setAgentVersionNumber(Number(e.target.value))}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 13,
                }}
              />
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 10,
            }}
          >
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: '#94a3b8',
                  marginBottom: 4,
                }}
              >
                Dynamic Var: customer_name
              </label>
              <input
                type="text"
                aria-label="Dynamic Variable customer_name"
                value={customerNameVar}
                onChange={(e) => setCustomerNameVar(e.target.value)}
                style={{
                  width: '100%',
                  padding: '7px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
            </div>
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: '#94a3b8',
                  marginBottom: 4,
                }}
              >
                Dynamic Var: preferred_time
              </label>
              <input
                type="text"
                aria-label="Dynamic Variable preferred_time"
                value={preferredTimeVar}
                onChange={(e) => setPreferredTimeVar(e.target.value)}
                style={{
                  width: '100%',
                  padding: '7px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
            </div>
          </div>

          {modeTab === 'llm' ? (
            <>
              <div>
                <label
                  style={{
                    display: 'block',
                    fontSize: 11,
                    color: '#94a3b8',
                    marginBottom: 4,
                  }}
                >
                  Optional System Prompt Override (leave blank to use pinned v
                  {agentVersionNumber} snapshot)
                </label>
                <textarea
                  rows={2}
                  aria-label="System Prompt Override"
                  placeholder="Optional prompt override..."
                  value={promptOverride}
                  onChange={(e) => setPromptOverride(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    borderRadius: 8,
                    border: '1px solid rgba(148, 163, 184, 0.3)',
                    background: '#0f172a',
                    color: '#f8fafc',
                    fontSize: 12,
                  }}
                />
              </div>

              <div>
                <label
                  style={{
                    display: 'block',
                    fontSize: 11,
                    color: '#94a3b8',
                    marginBottom: 4,
                  }}
                >
                  User Message
                </label>
                <textarea
                  rows={3}
                  aria-label="Playground User Message"
                  value={userMessage}
                  onChange={(e) => setUserMessage(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    borderRadius: 8,
                    border: '1px solid rgba(148, 163, 184, 0.3)',
                    background: '#0f172a',
                    color: '#f8fafc',
                    fontSize: 13,
                  }}
                />
              </div>
            </>
          ) : (
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: '#94a3b8',
                  marginBottom: 4,
                }}
              >
                Multi-Turn Caller Script (one utterance per line)
              </label>
              <textarea
                rows={5}
                aria-label="Multi-Turn Caller Script"
                value={multiTurnScript}
                onChange={(e) => setMultiTurnScript(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
            </div>
          )}

          {/* Inline Assertions Builder */}
          <div
            style={{
              padding: 12,
              borderRadius: 8,
              background: 'rgba(30, 41, 59, 0.65)',
            }}
          >
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: '#cbd5e1',
                marginBottom: 8,
              }}
            >
              Inline Evaluation Assertions ({inlineRules.length})
            </div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <select
                aria-label="Inline Rule Type"
                value={inlineRuleType}
                onChange={(e) =>
                  setInlineRuleType(e.target.value as EvaluationRuleType)
                }
                style={{
                  padding: '6px 8px',
                  borderRadius: 6,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              >
                <option value="contains">contains</option>
                <option value="not_contains">not_contains</option>
                <option value="regex">regex</option>
                <option value="tool_called">tool_called</option>
                <option value="tool_not_called">tool_not_called</option>
                <option value="final_state_equals">final_state_equals</option>
              </select>
              <input
                type="text"
                aria-label="Inline Rule Value"
                placeholder="Expected substring / tool / regex"
                value={inlineRuleValue}
                onChange={(e) => setInlineRuleValue(e.target.value)}
                style={{
                  flex: 1,
                  padding: '6px 8px',
                  borderRadius: 6,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
              <button
                type="button"
                onClick={handleAddInlineRule}
                style={{
                  padding: '6px 10px',
                  borderRadius: 6,
                  border: 'none',
                  background: '#475569',
                  color: '#ffffff',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                + Assert
              </button>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {inlineRules.map((r, i) => (
                <span
                  key={i}
                  style={{
                    padding: '3px 8px',
                    borderRadius: 6,
                    fontSize: 11,
                    background: 'rgba(59, 130, 246, 0.18)',
                    color: '#93c5fd',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  {r.name}
                  <button
                    type="button"
                    onClick={() =>
                      setInlineRules((prev) => prev.filter((_, idx) => idx !== i))
                    }
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: '#fca5a5',
                      cursor: 'pointer',
                      padding: 0,
                      fontSize: 11,
                    }}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>

          <button
            type="submit"
            data-testid="execute-playground-btn"
            disabled={running}
            style={{
              padding: '10px 18px',
              borderRadius: 8,
              border: 'none',
              background: running ? '#475569' : '#10b981',
              color: '#ffffff',
              fontWeight: 700,
              fontSize: 14,
              cursor: running ? 'not-allowed' : 'pointer',
            }}
          >
            {running
              ? 'Executing Against Pinned Version...'
              : modeTab === 'llm'
              ? `Run LLM Playground (Pinned v${agentVersionNumber})`
              : `Run Multi-Turn Simulation (Pinned v${agentVersionNumber})`}
          </button>

          {/* Recent Runs Selector */}
          {runs.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: '#94a3b8',
                  marginBottom: 6,
                }}
              >
                Recent Persisted Runs ({runs.length})
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {runs.slice(0, 5).map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => setSelectedRun(r)}
                    style={{
                      textAlign: 'left',
                      padding: '7px 10px',
                      borderRadius: 6,
                      border:
                        selectedRun?.id === r.id
                          ? '1px solid #3b82f6'
                          : '1px solid rgba(148, 163, 184, 0.16)',
                      background:
                        selectedRun?.id === r.id
                          ? 'rgba(59, 130, 246, 0.14)'
                          : 'rgba(15, 23, 42, 0.8)',
                      color: '#e2e8f0',
                      fontSize: 12,
                      cursor: 'pointer',
                    }}
                  >
                    <strong>{r.mode.toUpperCase()}</strong> · {r.agent_id} v
                    {r.agent_version_number} · Status: {r.status.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          )}
        </form>

        {/* Right Trace & Scorecard Viewer */}
        <SimulationTraceViewer
          run={selectedRun}
          evaluating={running}
          onRerunEvaluation={reevaluateRunOnly}
        />
      </div>
    </div>
  );
};

export default AgentPlayground;
