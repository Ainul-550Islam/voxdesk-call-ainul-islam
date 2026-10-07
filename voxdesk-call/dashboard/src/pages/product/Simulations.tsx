import React, { useState } from 'react';
import { useSimulations } from '../../hooks/useSimulations';
import { useCalls } from '../../hooks/useCalls';
import { BatchTestRunner } from '../../features/testing/BatchTestRunner';
import { SimulationTraceViewer } from '../../features/testing/SimulationTraceViewer';
import { WebCallTester } from '../../features/testing/WebCallTester';
import { PhoneCallTester } from '../../features/testing/PhoneCallTester';
import type { AgentKind, TestPassPolicy, TestRunMode } from '../../types/evaluation';

export const Simulations: React.FC = () => {
  const {
    suites,
    selectedSuite,
    setSelectedSuite,
    cases,
    selectedRun,
    setSelectedRun,
    lastBatchSummary,
    running,
    error,
    createSuite,
    createCase,
    removeCase,
    executeCase,
    executeBatchSuite,
    reevaluateRunOnly,
  } = useSimulations();

  const {
    readiness,
    activeWebCall,
    lastPhoneCall,
    busy: callBusy,
    startWebCall,
    sendEventToWebCall,
    runPhoneCallTest,
  } = useCalls();

  // Suite Creation State
  const [suiteName, setSuiteName] = useState('');
  const [suiteDescription, setSuiteDescription] = useState('');
  const [passPolicy, setPassPolicy] = useState<TestPassPolicy>('all_passed');
  const [minPassScore, setMinPassScore] = useState<number>(100);

  // Version-Pinned Test Case Creation State
  const [caseName, setCaseName] = useState('');
  const [caseAgentId, setCaseAgentId] = useState('default');
  const [caseAgentKind, setCaseAgentKind] = useState<AgentKind>('voice');
  const [caseVersionNumber, setCaseVersionNumber] = useState<number>(1);
  const [caseMode, setCaseMode] = useState<TestRunMode>('simulation');
  const [caseTurnsText, setCaseTurnsText] = useState(
    'Hello, I want to book an appointment tomorrow at 10:00 AM\nThank you, goodbye'
  );
  const [caseExpectedSubstring, setCaseExpectedSubstring] = useState('APT-2026');

  const handleCreateSuite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!suiteName.trim()) return;
    await createSuite({
      name: suiteName.trim(),
      description: suiteDescription.trim(),
      pass_policy: passPolicy,
      min_pass_score: Number(minPassScore) || 100,
    });
    setSuiteName('');
    setSuiteDescription('');
  };

  const handleCreateCase = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseName.trim() || !caseAgentId.trim()) return;
    const turns = caseTurnsText
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    const expectedRules = caseExpectedSubstring.trim()
      ? [
          {
            name: `Contains ${caseExpectedSubstring.trim()}`,
            rule_type: 'contains' as const,
            config: { substring: caseExpectedSubstring.trim() },
            enabled: true,
            weight: 1.0,
          },
        ]
      : [];
    await createCase({
      suite_id: selectedSuite?.id,
      name: caseName.trim(),
      agent_id: caseAgentId.trim(),
      agent_kind: caseAgentKind,
      agent_version_number: Number(caseVersionNumber) || 1,
      mode: caseMode,
      input_messages: turns,
      expected_rules: expectedRules,
      enabled: true,
    });
    setCaseName('');
  };

  return (
    <div
      data-testid="simulations-page"
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
      <div>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>
          Simulations, Batch Test Suites & Call Testing Studio
        </h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: '#94a3b8' }}>
          Create durable TestSuites and version-pinned TestCases, run batch
          evaluations, and test WebRTC / PSTN calls with full QA evidence.
        </p>
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

      {/* Top Row: Suite List + Create Suite + Create Pinned Case */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
          gap: 18,
        }}
      >
        {/* Suite Selector & Creator */}
        <div
          style={{
            background: 'rgba(15, 23, 42, 0.78)',
            border: '1px solid rgba(148, 163, 184, 0.2)',
            borderRadius: 12,
            padding: 18,
          }}
        >
          <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700 }}>
            Test Suites ({suites.length})
          </h3>
          <form
            onSubmit={handleCreateSuite}
            data-testid="create-suite-form"
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              marginBottom: 14,
            }}
          >
            <input
              type="text"
              aria-label="New Suite Name"
              placeholder="Suite name (e.g. Booking Regression Suite)"
              value={suiteName}
              onChange={(e) => setSuiteName(e.target.value)}
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 13,
              }}
            />
            <input
              type="text"
              aria-label="New Suite Description"
              placeholder="Optional description..."
              value={suiteDescription}
              onChange={(e) => setSuiteDescription(e.target.value)}
              style={{
                padding: '7px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <select
                aria-label="Suite Pass Policy"
                value={passPolicy}
                onChange={(e) =>
                  setPassPolicy(e.target.value as TestPassPolicy)
                }
                style={{
                  flex: 1,
                  padding: '7px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              >
                <option value="all_passed">All Assertions Passed</option>
                <option value="min_score">Minimum Weighted Score</option>
              </select>
              <input
                type="number"
                min="0"
                max="100"
                aria-label="Min Pass Score"
                value={minPassScore}
                onChange={(e) => setMinPassScore(Number(e.target.value))}
                style={{
                  width: 90,
                  padding: '7px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
              <button
                type="submit"
                data-testid="create-suite-submit-btn"
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
                + Create Suite
              </button>
            </div>
          </form>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {suites.map((s) => (
              <button
                key={s.id}
                type="button"
                data-testid={`select-suite-${s.id}`}
                onClick={() => setSelectedSuite(s)}
                style={{
                  textAlign: 'left',
                  padding: '9px 12px',
                  borderRadius: 8,
                  border:
                    selectedSuite?.id === s.id
                      ? '1px solid #3b82f6'
                      : '1px solid rgba(148, 163, 184, 0.16)',
                  background:
                    selectedSuite?.id === s.id
                      ? 'rgba(59, 130, 246, 0.15)'
                      : 'rgba(15, 23, 42, 0.9)',
                  color: '#f8fafc',
                  fontSize: 13,
                  cursor: 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span>
                  <strong>{s.name}</strong> ({s.case_count} cases)
                </span>
                {s.last_run_status && (
                  <span style={{ fontSize: 11, color: '#93c5fd' }}>
                    Last: {s.last_run_status.toUpperCase()}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Version-Pinned Test Case Creator */}
        <div
          style={{
            background: 'rgba(15, 23, 42, 0.78)',
            border: '1px solid rgba(148, 163, 184, 0.2)',
            borderRadius: 12,
            padding: 18,
          }}
        >
          <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700 }}>
            Add Version-Pinned Test Case
          </h3>
          <form
            onSubmit={handleCreateCase}
            data-testid="create-test-case-form"
            style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          >
            <input
              type="text"
              aria-label="Test Case Name"
              placeholder="Case name (e.g. Book Tomorrow 10AM)"
              value={caseName}
              onChange={(e) => setCaseName(e.target.value)}
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 13,
              }}
            />

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 95px 95px 110px',
                gap: 8,
              }}
            >
              <input
                type="text"
                aria-label="Test Case Agent ID"
                placeholder="Agent ID"
                value={caseAgentId}
                onChange={(e) => setCaseAgentId(e.target.value)}
                style={{
                  padding: '7px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
              <select
                aria-label="Test Case Agent Kind"
                value={caseAgentKind}
                onChange={(e) => setCaseAgentKind(e.target.value as AgentKind)}
                style={{
                  padding: '7px 8px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              >
                <option value="voice">voice</option>
                <option value="chat">chat</option>
              </select>
              <input
                type="number"
                min="1"
                aria-label="Test Case Pinned Version"
                title="Pinned AgentVersion Number"
                value={caseVersionNumber}
                onChange={(e) => setCaseVersionNumber(Number(e.target.value))}
                style={{
                  padding: '7px 8px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
              <select
                aria-label="Test Case Mode"
                value={caseMode}
                onChange={(e) => setCaseMode(e.target.value as TestRunMode)}
                style={{
                  padding: '7px 8px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              >
                <option value="simulation">simulation</option>
                <option value="llm">llm</option>
                <option value="chat">chat</option>
              </select>
            </div>

            <textarea
              rows={3}
              aria-label="Test Case User Turns"
              placeholder="One caller utterance per line..."
              value={caseTurnsText}
              onChange={(e) => setCaseTurnsText(e.target.value)}
              style={{
                padding: '7px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            />

            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type="text"
                aria-label="Test Case Expected Substring"
                placeholder="Expected substring assertion (e.g. APT-2026)"
                value={caseExpectedSubstring}
                onChange={(e) => setCaseExpectedSubstring(e.target.value)}
                style={{
                  flex: 1,
                  padding: '7px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(148, 163, 184, 0.3)',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: 12,
                }}
              />
              <button
                type="submit"
                data-testid="create-case-submit-btn"
                style={{
                  padding: '7px 14px',
                  borderRadius: 8,
                  border: 'none',
                  background: '#10b981',
                  color: '#ffffff',
                  fontWeight: 700,
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                + Pin & Save Case (v{caseVersionNumber})
              </button>
            </div>
          </form>
        </div>
      </div>

      {/* Batch Suite Runner */}
      <BatchTestRunner
        suite={selectedSuite}
        cases={cases}
        lastBatchSummary={lastBatchSummary}
        running={running}
        onRunBatch={executeBatchSuite}
        onRunSingleCase={executeCase}
        onSelectRun={setSelectedRun}
        onDeleteCase={removeCase}
      />

      {/* Web Call & Phone Call Testers */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))',
          gap: 18,
        }}
      >
        <WebCallTester
          agentId={caseAgentId || 'default'}
          agentVersionNumber={caseVersionNumber || 1}
          readiness={readiness}
          activeSession={activeWebCall}
          busy={callBusy}
          onStartSession={async (p) => {
            const r = await startWebCall(p);
            if (r) setSelectedRun(r);
            return r;
          }}
          onSendEvent={async (runId, ev) => {
            const r = await sendEventToWebCall(runId, ev);
            if (r) setSelectedRun(r);
            return r;
          }}
        />

        <PhoneCallTester
          agentId={caseAgentId || 'default'}
          agentVersionNumber={caseVersionNumber || 1}
          readiness={readiness}
          lastPhoneCall={lastPhoneCall}
          busy={callBusy}
          onRunPhoneCall={async (p) => {
            const r = await runPhoneCallTest(p);
            if (r) setSelectedRun(r);
            return r;
          }}
        />
      </div>

      {/* Trace & QA Evidence Viewer */}
      <SimulationTraceViewer
        run={selectedRun}
        evaluating={running}
        onRerunEvaluation={reevaluateRunOnly}
      />
    </div>
  );
};

export default Simulations;
