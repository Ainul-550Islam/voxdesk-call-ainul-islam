import React, { useEffect, useState } from 'react';
import { useEvaluations } from '../../hooks/useEvaluations';
import { useSimulations } from '../../hooks/useSimulations';
import { EvaluationRulesEditor } from '../../features/testing/EvaluationRulesEditor';
import { SimulationTraceViewer } from '../../features/testing/SimulationTraceViewer';
import { ConductorPanel } from '../../features/conductor/ConductorPanel';

export const QAScorecards: React.FC = () => {
  const {
    rules,
    scorecardDetail,
    agentSummary,
    versionComparison,
    loading,
    evaluating,
    error: evalError,
    addRule,
    editRule,
    removeRule,
    loadRunScorecard,
    rerunEvaluation,
    loadAgentSummary,
    compareVersions,
  } = useEvaluations();

  const {
    suites,
    selectedSuite,
    setSelectedSuite,
    runs,
    selectedRun,
    setSelectedRun,
    refreshRun,
  } = useSimulations();

  const [agentIdForCompare, setAgentIdForCompare] = useState('default');
  const [versionA, setVersionA] = useState<number>(1);
  const [versionB, setVersionB] = useState<number>(2);

  useEffect(() => {
    if (selectedRun) {
      void loadRunScorecard(selectedRun.id);
    }
  }, [selectedRun, loadRunScorecard]);

  const handleRerunOnly = async (runId: string) => {
    const updated = await rerunEvaluation(runId);
    if (updated) {
      setSelectedRun(updated);
    } else {
      await refreshRun(runId);
    }
  };

  const handleCompareVersions = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agentIdForCompare.trim()) return;
    await loadAgentSummary(agentIdForCompare.trim());
    await compareVersions(
      agentIdForCompare.trim(),
      Number(versionA) || 1,
      Number(versionB) || 2
    );
  };

  return (
    <div
      data-testid="qa-scorecards-page"
      style={{
        padding: 24,
        color: '#f8fafc',
        maxWidth: 1440,
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
            QA Scorecards, Evaluation Rules & Version Quality Evidence
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: '#94a3b8' }}>
            Inspect evidence-backed QA scorecards, manage deterministic &
            LLM-as-judge assertions, and compare quality across pinned
            AgentVersions.
          </p>
        </div>

        {suites.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>Scope Suite:</span>
            <select
              aria-label="Filter Suite Scope"
              value={selectedSuite?.id || ''}
              onChange={(e) => {
                const match = suites.find((s) => s.id === e.target.value);
                setSelectedSuite(match || null);
              }}
              style={{
                padding: '7px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            >
              {suites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {evalError && (
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
          {evalError}
        </div>
      )}

      {/* Evaluation Rules Editor */}
      <EvaluationRulesEditor
        rules={rules}
        suiteId={selectedSuite?.id}
        loading={loading}
        onCreateRule={addRule}
        onUpdateRule={editRule}
        onDeleteRule={removeRule}
      />

      {/* Agent Version Quality Comparison */}
      <div
        data-testid="version-qa-comparison-card"
        style={{
          background: 'rgba(15, 23, 42, 0.78)',
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
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 14,
          }}
        >
          <div>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>
              Agent Version-over-Version QA Comparison
            </h3>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>
              Compare pass rates, assertion failures, and latency between two
              immutable AgentVersions.
            </p>
          </div>

          <form
            onSubmit={handleCompareVersions}
            style={{ display: 'flex', alignItems: 'center', gap: 8 }}
          >
            <input
              type="text"
              aria-label="Compare Agent ID"
              placeholder="Agent ID (e.g. default)"
              value={agentIdForCompare}
              onChange={(e) => setAgentIdForCompare(e.target.value)}
              style={{
                width: 150,
                padding: '7px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            />
            <input
              type="number"
              min="1"
              aria-label="Baseline Version A"
              value={versionA}
              onChange={(e) => setVersionA(Number(e.target.value))}
              style={{
                width: 75,
                padding: '7px 8px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            />
            <span style={{ fontSize: 12, color: '#94a3b8' }}>vs</span>
            <input
              type="number"
              min="1"
              aria-label="Candidate Version B"
              value={versionB}
              onChange={(e) => setVersionB(Number(e.target.value))}
              style={{
                width: 75,
                padding: '7px 8px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            />
            <button
              type="submit"
              data-testid="compare-versions-qa-btn"
              style={{
                padding: '7px 14px',
                borderRadius: 8,
                border: 'none',
                background: '#3b82f6',
                color: '#ffffff',
                fontWeight: 600,
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              Compare v{versionA} vs v{versionB}
            </button>
          </form>
        </div>

        {agentSummary && (
          <div
            data-testid="agent-qa-summary-bar"
            style={{
              display: 'flex',
              gap: 16,
              flexWrap: 'wrap',
              padding: 12,
              borderRadius: 8,
              background: 'rgba(30, 41, 59, 0.7)',
              fontSize: 12,
              color: '#cbd5e1',
              marginBottom: versionComparison ? 12 : 0,
            }}
          >
            <span>
              Total Runs: <strong>{agentSummary.total_runs}</strong>
            </span>
            <span style={{ color: '#34d399' }}>
              Passed: <strong>{agentSummary.passed_runs}</strong>
            </span>
            <span style={{ color: '#f87171' }}>
              Failed Assertions:{' '}
              <strong>{agentSummary.failed_assertion_runs}</strong>
            </span>
            <span style={{ color: '#fbbf24' }}>
              Errors: <strong>{agentSummary.error_runs}</strong>
            </span>
            <span>
              NO_ASSERTIONS: <strong>{agentSummary.no_assertion_runs}</strong>
            </span>
            <span>
              Pass Rate:{' '}
              <strong>
                {agentSummary.pass_rate_pct !== null
                  ? `${agentSummary.pass_rate_pct}%`
                  : 'N/A'}
              </strong>
            </span>
          </div>
        )}

        {versionComparison && (
          <div
            data-testid="version-comparison-results"
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr 1fr',
              gap: 12,
              fontSize: 12,
            }}
          >
            <div
              style={{
                padding: 12,
                borderRadius: 8,
                background: 'rgba(15, 23, 42, 0.9)',
                border: '1px solid rgba(148, 163, 184, 0.16)',
              }}
            >
              <strong>v{versionComparison.version_a.agent_version_number}</strong>
              <div>Runs: {versionComparison.version_a.total_runs}</div>
              <div>
                Avg Score:{' '}
                {versionComparison.version_a.average_score !== null
                  ? `${versionComparison.version_a.average_score}%`
                  : 'NO_ASSERTIONS'}
              </div>
            </div>
            <div
              style={{
                padding: 12,
                borderRadius: 8,
                background: 'rgba(15, 23, 42, 0.9)',
                border: '1px solid rgba(148, 163, 184, 0.16)',
              }}
            >
              <strong>v{versionComparison.version_b.agent_version_number}</strong>
              <div>Runs: {versionComparison.version_b.total_runs}</div>
              <div>
                Avg Score:{' '}
                {versionComparison.version_b.average_score !== null
                  ? `${versionComparison.version_b.average_score}%`
                  : 'NO_ASSERTIONS'}
              </div>
            </div>
            <div
              style={{
                padding: 12,
                borderRadius: 8,
                background: 'rgba(59, 130, 246, 0.12)',
                border: '1px solid rgba(59, 130, 246, 0.3)',
              }}
            >
              <strong>Delta (vB - vA)</strong>
              <div>
                Score Delta:{' '}
                {versionComparison.score_delta !== null
                  ? `${versionComparison.score_delta > 0 ? '+' : ''}${
                      versionComparison.score_delta
                    }%`
                  : 'N/A'}
              </div>
              <div>
                Pass Rate Delta:{' '}
                {versionComparison.pass_rate_delta_pct !== null
                  ? `${versionComparison.pass_rate_delta_pct > 0 ? '+' : ''}${
                      versionComparison.pass_rate_delta_pct
                    }%`
                  : 'N/A'}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Run List & Selected Run QA Scorecard Detail */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(300px, 0.9fr) minmax(440px, 1.5fr)',
          gap: 20,
          alignItems: 'start',
        }}
      >
        <div
          style={{
            background: 'rgba(15, 23, 42, 0.78)',
            border: '1px solid rgba(148, 163, 184, 0.2)',
            borderRadius: 12,
            padding: 18,
          }}
        >
          <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700 }}>
            Persisted TestRuns ({runs.length})
          </h3>
          {runs.length === 0 ? (
            <div style={{ fontSize: 12, color: '#94a3b8' }}>
              No persisted TestRuns yet.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {runs.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  data-testid={`qa-select-run-${r.id}`}
                  onClick={() => setSelectedRun(r)}
                  style={{
                    textAlign: 'left',
                    padding: '10px 12px',
                    borderRadius: 8,
                    border:
                      selectedRun?.id === r.id
                        ? '1px solid #3b82f6'
                        : '1px solid rgba(148, 163, 184, 0.16)',
                    background:
                      selectedRun?.id === r.id
                        ? 'rgba(59, 130, 246, 0.15)'
                        : 'rgba(15, 23, 42, 0.9)',
                    color: '#f8fafc',
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontWeight: 700,
                    }}
                  >
                    <span>
                      {r.agent_id} · v{r.agent_version_number} ({r.mode})
                    </span>
                    <span>
                      {r.scorecard_summary?.status || r.status.toUpperCase()}
                    </span>
                  </div>
                  <div style={{ marginTop: 4, fontSize: 11, color: '#94a3b8' }}>
                    Score:{' '}
                    {r.scorecard_summary?.overall_score !== null &&
                    r.scorecard_summary?.overall_score !== undefined
                      ? `${r.scorecard_summary.overall_score}%`
                      : 'NO_ASSERTIONS'}{' '}
                    · {r.is_mock_provider ? 'SANDBOX MOCK' : 'LIVE'}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {scorecardDetail && (
            <div
              data-testid="qa-scorecard-detail-header"
              style={{
                padding: 14,
                borderRadius: 10,
                background: 'rgba(30, 41, 59, 0.75)',
                border: '1px solid rgba(148, 163, 184, 0.2)',
                fontSize: 12,
                color: '#e2e8f0',
              }}
            >
              <strong>QA Scorecard Endpoint Record:</strong> Status ={' '}
              <code data-testid="qa-detail-status">
                {scorecardDetail.scorecard.status}
              </code>{' '}
              · Score ={' '}
              <code data-testid="qa-detail-score">
                {scorecardDetail.scorecard.overall_score !== null
                  ? `${scorecardDetail.scorecard.overall_score}%`
                  : 'NO_ASSERTIONS'}
              </code>{' '}
              · Formula = <code>{scorecardDetail.scorecard.formula_version}</code>
            </div>
          )}

          <SimulationTraceViewer
            run={selectedRun}
            evaluating={evaluating}
            onRerunEvaluation={handleRerunOnly}
          />

          <ConductorPanel
            agentId={selectedRun?.agent_id || agentIdForCompare || ''}
            agentKind="voice"
            baseVersionNumber={selectedRun?.agent_version_number}
            originSurface="qa_scorecard"
            testRunIds={selectedRun ? [selectedRun.id] : []}
          />
        </div>
      </div>
    </div>
  );
};

export default QAScorecards;
