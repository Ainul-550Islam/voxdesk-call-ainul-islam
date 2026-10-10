"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface TestSuiteRecord {
  id: string;
  name: string;
  description: string;
  agent_id: string;
  agent_kind: string;
  target_version_number: number | null;
  case_count: number;
}

interface TestCaseRecord {
  id: string;
  suite_id: string | null;
  name: string;
  description: string;
  agent_id: string;
  agent_version_number: number;
  metadata?: Record<string, unknown>;
}

interface TestRunRecord {
  id: string;
  test_case_id: string;
  agent_id: string;
  agent_version_number: number;
  status: string;
  is_mock_provider: boolean;
  provider: string;
  model: string;
  duration_ms: number;
  transcript_snapshot: Array<{
    turn_index: number;
    user_input: string;
    assistant_response: string;
  }>;
  evaluation_summary?: {
    total_rules?: number;
    passed_rules?: number;
    failed_rules?: number;
    pass_rate?: number;
    judge_verdict?: {
      passed: boolean;
      goal_achieved: boolean;
      overall_score: number;
      rationale: string;
    };
  };
}

interface SimulationKpis {
  total_runs: number;
  live_runs: number;
  excluded_mock_runs: number;
  passed_runs: number;
  failed_runs: number;
  pass_rate_percent: number;
}

export default function TestsPage() {
  const [suites, setSuites] = useState<TestSuiteRecord[]>([]);
  const [cases, setCases] = useState<TestCaseRecord[]>([]);
  const [runs, setRuns] = useState<TestRunRecord[]>([]);
  const [kpis, setKpis] = useState<SimulationKpis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Scenario editor state (scripted or simulated_caller)
  const [scenarioMode, setScenarioMode] = useState<"simulated_caller" | "scripted">(
    "simulated_caller",
  );
  const [scenarioName, setScenarioName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [versionNum, setVersionNum] = useState(1);
  const [persona, setPersona] = useState("Busy patient looking to reschedule");
  const [goal, setGoal] = useState("Book an appointment for Tuesday afternoon");
  const [interruptionStyle, setInterruptionStyle] = useState("none");
  const [seed, setSeed] = useState(42);
  const [criteriaText, setCriteriaText] = useState(
    "Confirm appointment slot\nProvide polite closing",
  );
  const [scriptedTurnsText, setScriptedTurnsText] = useState(
    "Hello, what are your office hours?\nCan I book an appointment for Tuesday at 2pm?",
  );

  // Regression from call ID
  const [regressionCallId, setRegressionCallId] = useState("");
  const [regressionBanner, setRegressionBanner] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [suitesRes, casesRes, runsRes, kpisRes] = await Promise.all([
        request<TestSuiteRecord[]>("/api/v1/testing/suites"),
        request<TestCaseRecord[]>("/api/v1/testing/cases"),
        request<TestRunRecord[]>("/api/v1/testing/runs"),
        request<SimulationKpis>("/api/v1/testing/kpis"),
      ]);
      setSuites(suitesRes);
      setCases(casesRes);
      setRuns(runsRes);
      setKpis(kpisRes);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load test suites",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    void loadAll();
  }, [loadAll]);

  async function handleRunScenario(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!agentId.trim()) {
      setError("Target Agent ID is required.");
      return;
    }
    try {
      if (scenarioMode === "simulated_caller") {
        const criteria = criteriaText
          .split(/\r?\n/)
          .map((c) => c.trim())
          .filter(Boolean);
        await request("/api/v1/testing/simulations/caller-run", {
          method: "POST",
          body: JSON.stringify({
            name: scenarioName.trim() || "Simulated Caller Scenario",
            agent_id: agentId.trim(),
            agent_kind: "voice",
            agent_version_number: versionNum,
            persona,
            goal,
            interruption_style: interruptionStyle,
            seed,
            max_turns: 4,
            success_criteria: criteria,
            allow_mock_fallback: true,
          }),
        });
      } else {
        const lines = scriptedTurnsText
          .split(/\r?\n/)
          .map((l) => l.trim())
          .filter(Boolean);
        const createdCase = await request<TestCaseRecord>(
          "/api/v1/testing/cases",
          {
            method: "POST",
            body: JSON.stringify({
              name: scenarioName.trim() || "Scripted Scenario",
              agent_id: agentId.trim(),
              agent_kind: "voice",
              agent_version_number: versionNum,
              turns: lines.map((u) => ({ user_input: u })),
              expected_rules: [],
            }),
          },
        );
        await request("/api/v1/testing/runs/case", {
          method: "POST",
          body: JSON.stringify({
            test_case_id: createdCase.id,
            agent_version_number: versionNum,
            allow_mock_fallback: true,
          }),
        });
      }
      await loadAll();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to run test scenario",
      );
    }
  }

  async function handleCreateRegressionFromCall(e: React.FormEvent) {
    e.preventDefault();
    if (!regressionCallId.trim()) return;
    setError(null);
    setRegressionBanner(null);
    try {
      const res = await request<{
        test_case: { id: string; name: string };
        evaluation_rules: Array<{ id: string }>;
        redaction: { total_redactions: number };
      }>(
        `/api/v1/testing/regressions/from-call/${encodeURIComponent(
          regressionCallId.trim(),
        )}`,
        {
          method: "POST",
          body: JSON.stringify({ redact_pii: true }),
        },
      );
      setRegressionBanner(
        `Created regression test "${res.test_case.name}" (${res.evaluation_rules.length} rules marked needs_review, ${res.redaction.total_redactions} PII redactions).`,
      );
      setRegressionCallId("");
      await loadAll();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to create regression test from call",
      );
    }
  }

  return (
    <div data-testid="tests-page">
      <header className="page-head">
        <div>
          <h1>Simulation Test Suites &amp; Regression QA</h1>
          <p className="muted">
            Run deterministic LLM-played caller simulations or scripted scenarios against pinned AgentVersions, and convert production calls into PII-redacted regression tests.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      {regressionBanner ? (
        <div className="card" data-testid="regression-created-notice">
          {regressionBanner}
        </div>
      ) : null}

      {kpis ? (
        <section className="card" data-testid="testing-kpis-card">
          <h2>Pass-Rate KPIs (Mock Runs Excluded)</h2>
          <dl className="kv">
            <div>
              <dt>Live Pass Rate</dt>
              <dd data-testid="live-pass-rate">{kpis.pass_rate_percent}%</dd>
            </div>
            <div>
              <dt>Live Runs</dt>
              <dd>{kpis.live_runs}</dd>
            </div>
            <div>
              <dt>Passed / Failed (Live)</dt>
              <dd>
                {kpis.passed_runs} / {kpis.failed_runs}
              </dd>
            </div>
            <div>
              <dt>Excluded Mock Runs</dt>
              <dd data-testid="excluded-mock-runs">{kpis.excluded_mock_runs}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      <section className="card" data-testid="scenario-editor-card">
        <h2>Scenario Editor (Simulated Caller or Scripted)</h2>
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
          <button
            type="button"
            className={scenarioMode === "simulated_caller" ? "btn primary" : "btn"}
            data-testid="mode-simulated-caller-btn"
            onClick={() => setScenarioMode("simulated_caller")}
          >
            LLM Simulated Caller
          </button>
          <button
            type="button"
            className={scenarioMode === "scripted" ? "btn primary" : "btn"}
            data-testid="mode-scripted-btn"
            onClick={() => setScenarioMode("scripted")}
          >
            Scripted Turns
          </button>
        </div>

        <form onSubmit={handleRunScenario} style={{ display: "grid", gap: "0.75rem" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <label>
              Scenario Name
              <input
                type="text"
                data-testid="scenario-name-input"
                placeholder="Urgent Dental Reschedule"
                value={scenarioName}
                onChange={(e) => setScenarioName(e.target.value)}
              />
            </label>
            <label>
              Target Agent ID
              <input
                type="text"
                data-testid="scenario-agent-input"
                placeholder="Agent UUID"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
              />
            </label>
            <label>
              Pinned Agent Version
              <input
                type="number"
                min={1}
                value={versionNum}
                onChange={(e) => setVersionNum(Number(e.target.value))}
              />
            </label>
          </div>

          {scenarioMode === "simulated_caller" ? (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                  gap: "0.75rem",
                }}
              >
                <label>
                  Caller Persona
                  <input
                    type="text"
                    data-testid="caller-persona-input"
                    value={persona}
                    onChange={(e) => setPersona(e.target.value)}
                  />
                </label>
                <label>
                  Caller Goal
                  <input
                    type="text"
                    data-testid="caller-goal-input"
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                  />
                </label>
                <label>
                  Interruption Style
                  <select
                    data-testid="interruption-style-select"
                    value={interruptionStyle}
                    onChange={(e) => setInterruptionStyle(e.target.value)}
                  >
                    <option value="none">None</option>
                    <option value="polite">Polite</option>
                    <option value="frequent">Frequent Barge-In</option>
                    <option value="impatient">Impatient</option>
                  </select>
                </label>
                <label>
                  Deterministic Seed
                  <input
                    type="number"
                    data-testid="caller-seed-input"
                    value={seed}
                    onChange={(e) => setSeed(Number(e.target.value))}
                  />
                </label>
              </div>
              <label>
                Success Criteria (one per line)
                <textarea
                  rows={3}
                  data-testid="caller-criteria-textarea"
                  value={criteriaText}
                  onChange={(e) => setCriteriaText(e.target.value)}
                />
              </label>
            </>
          ) : (
            <label>
              Scripted Caller Turns (one turn per line)
              <textarea
                rows={4}
                data-testid="scripted-turns-textarea"
                value={scriptedTurnsText}
                onChange={(e) => setScriptedTurnsText(e.target.value)}
              />
            </label>
          )}

          <div>
            <button
              type="submit"
              className="btn primary"
              data-testid="run-scenario-btn"
            >
              Run Scenario
            </button>
          </div>
        </form>
      </section>

      <section className="card" data-testid="regression-from-call-card">
        <h2>Create Regression Test from Production Call</h2>
        <form
          onSubmit={handleCreateRegressionFromCall}
          style={{ display: "flex", gap: "0.75rem", alignItems: "end" }}
        >
          <label style={{ flex: 1 }}>
            Production Call ID
            <input
              type="text"
              data-testid="regression-call-id-input"
              placeholder="Call UUID from /dashboard/calls/[id]"
              value={regressionCallId}
              onChange={(e) => setRegressionCallId(e.target.value)}
            />
          </label>
          <button
            type="submit"
            className="btn"
            data-testid="create-regression-btn"
          >
            Convert Call to Regression Test
          </button>
        </form>
      </section>

      <section className="card" data-testid="test-runs-card">
        <h2>Recent Simulation &amp; Regression Runs ({runs.length})</h2>
        {loading ? (
          <div className="loading">Loading runs…</div>
        ) : runs.length === 0 ? (
          <div className="empty">No simulation runs recorded yet.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Agent / Version</th>
                <th>Provider</th>
                <th>Turns</th>
                <th>Verdict / Rationale</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className={`badge ${r.status}`}>{r.status}</span>
                  </td>
                  <td>
                    {r.agent_id} (v{r.agent_version_number})
                  </td>
                  <td>
                    {r.provider}/{r.model}{" "}
                    {r.is_mock_provider ? "(mock)" : "(live)"}
                  </td>
                  <td>{r.transcript_snapshot?.length ?? 0}</td>
                  <td>
                    {r.evaluation_summary?.judge_verdict?.rationale ??
                      `${r.evaluation_summary?.passed_rules ?? 0}/${
                        r.evaluation_summary?.total_rules ?? 0
                      } rules passed`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>Test Suites ({suites.length}) &amp; Cases ({cases.length})</h2>
        <ul>
          {cases.map((tc) => (
            <li key={tc.id}>
              <strong>{tc.name}</strong> — v{tc.agent_version_number}{" "}
              {tc.metadata?.needs_review ? (
                <span className="badge">needs_review</span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
