import React, { useEffect, useState } from 'react';
import { listAgents } from '../../api/agents';
import { ConductorPanel } from '../../features/conductor/ConductorPanel';

export const ConductorPage: React.FC = () => {
  const [agents, setAgents] = useState<
    Array<{
      id: string;
      name: string;
      active_version_number?: number;
      status?: string;
    }>
  >([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>('');
  const [originSurface, setOriginSurface] = useState<
    'agent_builder' | 'agent_detail' | 'qa_scorecard' | 'call_detail'
  >('agent_builder');
  const [callIdFilter, setCallIdFilter] = useState<string>('');
  const [testRunIdFilter, setTestRunIdFilter] = useState<string>('');

  useEffect(() => {
    let mounted = true;
    Promise.resolve(listAgents?.())
      .then((res: unknown) => {
        if (!mounted) return;
        const rawAgents = Array.isArray(res)
          ? res
          : res &&
            typeof res === 'object' &&
            Array.isArray((res as { agents?: unknown[] }).agents)
          ? (res as { agents: Array<{ id: string; name: string; active_version_number?: number; status?: string }> }).agents
          : [];
        setAgents(rawAgents);
        if (rawAgents.length > 0 && !selectedAgentId) {
          setSelectedAgentId(rawAgents[0].id);
        }
      })
      .catch(() => {
        // ignore
      });
    return () => {
      mounted = false;
    };
  }, [selectedAgentId]);

  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  return (
    <div
      data-testid="conductor-page"
      style={{
        padding: 28,
        maxWidth: 1360,
        margin: '0 auto',
        color: '#f8fafc',
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
          gap: 14,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>
            Conductor Studio — AI Agent Build, Review, Test & Safe Apply
          </h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: '#94a3b8' }}>
            Permission-scoped AI copilot for proposing, validating, simulating,
            and reviewing granular configuration changes before creating a new
            immutable AgentVersion.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <select
            aria-label="Target Agent"
            data-testid="conductor-agent-select"
            value={selectedAgentId}
            onChange={(e) => setSelectedAgentId(e.target.value)}
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 13,
            }}
          >
            {agents.length === 0 && (
              <option value="">No Voice Agents Found</option>
            )}
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} (v{a.active_version_number || 1})
              </option>
            ))}
          </select>

          <select
            aria-label="Originating Surface"
            value={originSurface}
            onChange={(e) =>
              setOriginSurface(
                e.target.value as
                  | 'agent_builder'
                  | 'agent_detail'
                  | 'qa_scorecard'
                  | 'call_detail'
              )
            }
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 13,
            }}
          >
            <option value="agent_builder">Agent Builder Context</option>
            <option value="agent_detail">Agent Detail Context</option>
            <option value="qa_scorecard">QA Scorecard Context</option>
            <option value="call_detail">Call Detail Context</option>
          </select>
        </div>
      </div>

      {/* Optional Scoped Evidence Input Bar */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 12,
          padding: 14,
          borderRadius: 10,
          background: 'rgba(15, 23, 42, 0.75)',
          border: '1px solid rgba(148, 163, 184, 0.2)',
        }}
      >
        <div>
          <label
            style={{
              display: 'block',
              fontSize: 11,
              fontWeight: 700,
              color: '#94a3b8',
              marginBottom: 4,
            }}
          >
            SCOPED CALL IDS (COMMA-SEPARATED, OPTIONAL)
          </label>
          <input
            type="text"
            placeholder="e.g. call-uuid-1, call-uuid-2"
            value={callIdFilter}
            onChange={(e) => setCallIdFilter(e.target.value)}
            style={{
              width: '100%',
              padding: '7px 10px',
              borderRadius: 6,
              border: '1px solid rgba(148, 163, 184, 0.28)',
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
              fontWeight: 700,
              color: '#94a3b8',
              marginBottom: 4,
            }}
          >
            SCOPED TEST RUN IDS (COMMA-SEPARATED, OPTIONAL)
          </label>
          <input
            type="text"
            placeholder="e.g. run-uuid-1"
            value={testRunIdFilter}
            onChange={(e) => setTestRunIdFilter(e.target.value)}
            style={{
              width: '100%',
              padding: '7px 10px',
              borderRadius: 6,
              border: '1px solid rgba(148, 163, 184, 0.28)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 12,
            }}
          />
        </div>
      </div>

      <ConductorPanel
        agentId={selectedAgentId}
        agentKind="voice"
        baseVersionNumber={selectedAgent?.active_version_number}
        originSurface={originSurface}
        callIds={callIdFilter
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)}
        testRunIds={testRunIdFilter
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)}
      />
    </div>
  );
};

export default ConductorPage;
