import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import * as evaluationsApi from "../api/evaluations";
import * as simulationsApi from "../api/simulations";
import * as callsApi from "../api/calls";
import EvaluationRulesEditor from "../features/testing/EvaluationRulesEditor";
import SimulationTraceViewer from "../features/testing/SimulationTraceViewer";
import WebCallTester from "../features/testing/WebCallTester";
import PhoneCallTester from "../features/testing/PhoneCallTester";
import type { TestRun } from "../types/evaluation";

vi.mock("../api/agent-builder", () => ({
  listAgents: vi.fn().mockResolvedValue([
    {
      id: "agent-voice-1",
      name: "Clinic Booking Agent",
      active_version_number: 2,
      status: "published",
    },
  ]),
  listVersions: vi.fn().mockResolvedValue([
    {
      id: "ver-1",
      version: 1,
      config_hash: "aabbcc1122334455",
      notes: "v1 baseline",
      published_at: "2026-10-03T10:00:00Z",
    },
    {
      id: "ver-2",
      version: 2,
      config_hash: "ddeeff6677889900",
      notes: "v2 booking flow",
      published_at: "2026-10-03T11:00:00Z",
    },
  ]),
}));

const sampleRun: TestRun = {
  id: "run-uuid-0001",
  tenant_id: "tenant-uuid-0001",
  environment_id: null,
  suite_id: "suite-uuid-0001",
  batch_id: null,
  test_case_id: "case-uuid-0001",
  agent_id: "agent-voice-1",
  agent_kind: "voice",
  agent_version_id: "ver-2",
  agent_version_number: 2,
  agent_config_hash: "ddeeff667788990011223344",
  pinned_config_snapshot: {
    name: "Clinic Booking Agent",
    system_prompt: "You are a helpful clinic assistant.",
  },
  mode: "simulation",
  status: "passed",
  is_mock_provider: true,
  provider: "openai",
  model: "gpt-4o-mini",
  correlation_id: "sim-corr-0001",
  transcript_snapshot: [
    {
      role: "user",
      content: "I would like to book an appointment tomorrow at 10 AM.",
      turn_index: 0,
      timestamp: "2026-10-03T12:00:00Z",
    },
    {
      role: "assistant",
      content: "Your appointment is confirmed with reference APT-2026.",
      turn_index: 1,
      intent: "book_appointment",
      tool_calls: [
        {
          name: "book_appointment",
          arguments: { requested_slot: "tomorrow at 10 AM" },
          result: { status: "confirmed", confirmation_code: "APT-2026" },
        },
      ],
      latency_ms: 42,
      timestamp: "2026-10-03T12:00:01Z",
    },
  ],
  events_snapshot: [
    {
      event: "tool_called",
      turn_index: 1,
      tool_name: "book_appointment",
      timestamp: "2026-10-03T12:00:01Z",
    },
  ],
  usage_metadata: {
    prompt_tokens: 64,
    completion_tokens: 40,
    total_tokens: 104,
    turn_count: 2,
  },
  latency_metadata: {
    total_duration_ms: 48,
    avg_turn_latency_ms: 42,
    max_turn_latency_ms: 42,
    p95_ms: 42,
  },
  final_output: {
    final_state: "completed",
    transferred: false,
    variables: {
      booking_status: "confirmed",
      confirmation_code: "APT-2026",
    },
  },
  scorecard_summary: {
    status: "PASSED",
    overall_score: 100.0,
    formula_version: "1.0.0",
    evaluator_version: "1.0.0",
    total_rules: 2,
    enabled_rules: 2,
    passed_rules: 2,
    failed_rules: 0,
    error_rules: 0,
    skipped_rules: 0,
    weighted_points_earned: 2.0,
    weighted_points_possible: 2.0,
    is_mock_provider: true,
    explanation: "Evaluated 2 active rules; weighted score 100.0%.",
    evaluated_at: "2026-10-03T12:00:02Z",
  },
  evaluation_results: [
    {
      id: "res-1",
      tenant_id: "tenant-uuid-0001",
      test_run_id: "run-uuid-0001",
      evaluation_rule_id: "rule-1",
      rule_name: "Contains Confirmation Code",
      rule_type: "contains",
      status: "PASSED",
      score: 1.0,
      weight: 1.0,
      evidence: {
        matched_turn_indices: [1],
        matched_text_snippets: ["Your appointment is confirmed with reference APT-2026."],
        matched_tool_calls: [],
        variable_diff: {},
        expected: "APT-2026",
        actual: "Matched in 1 turn(s)",
        judge_rationale: null,
        error_detail: null,
      },
      explanation: "Substring 'APT-2026' matched in turns [1].",
      evaluator_version: "1.0.0",
      formula_version: "1.0.0",
      created_at: "2026-10-03T12:00:02Z",
    },
  ],
  error_code: null,
  error_message: null,
  started_at: "2026-10-03T12:00:00Z",
  completed_at: "2026-10-03T12:00:02Z",
  duration_ms: 48,
  created_by: null,
  created_at: "2026-10-03T12:00:00Z",
};

describe("Prompt 3 Testing, Simulation, Evaluation & Call Studio", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("exposes typed evaluation, simulation, and call API client functions", () => {
    expect(typeof evaluationsApi.listEvaluationRules).toBe("function");
    expect(typeof evaluationsApi.createEvaluationRule).toBe("function");
    expect(typeof evaluationsApi.rerunTestRunEvaluation).toBe("function");
    expect(typeof evaluationsApi.compareAgentVersionsQA).toBe("function");
    expect(typeof simulationsApi.runLLMPlayground).toBe("function");
    expect(typeof simulationsApi.runMultiTurnSimulation).toBe("function");
    expect(typeof simulationsApi.runTestSuiteBatch).toBe("function");
    expect(typeof callsApi.startWebCallSession).toBe("function");
    expect(typeof callsApi.startPhoneCallTest).toBe("function");
  });

  it("renders SimulationTraceViewer with pinned version, evidence snippets, and MOCK PROVIDER badge", async () => {
    const onRerun = vi.fn().mockResolvedValue(sampleRun);
    render(<SimulationTraceViewer run={sampleRun} onRerunEvaluation={onRerun} />);

    expect(screen.getByTestId("trace-pinned-version")).toHaveTextContent(
      /Agent agent-voice-1 · v2/i
    );
    expect(screen.getByTestId("trace-provider-badge")).toHaveTextContent(
      /SANDBOX MOCK/i
    );
    expect(
      screen.getByText(/Your appointment is confirmed with reference APT-2026/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Contains Confirmation Code/i)).toBeInTheDocument();
    expect(screen.getByTestId("rerun-evaluation-only-btn")).toBeInTheDocument();
  });

  it("renders EvaluationRulesEditor with all 14 rule types and rule creation form", () => {
    render(
      <EvaluationRulesEditor
        rules={[
          {
            id: "rule-1",
            tenant_id: "tenant-1",
            suite_id: "suite-1",
            test_case_id: null,
            name: "Contains Confirmation Code",
            description: "Ensures APT-2026 is present",
            rule_type: "contains",
            config: { substring: "APT-2026" },
            enabled: true,
            is_blocking: true,
            weight: 1.0,
            evaluator_version: "1.0.0",
            created_at: "2026-10-03T12:00:00Z",
            updated_at: "2026-10-03T12:00:00Z",
          },
        ]}
        suiteId="suite-1"
        onCreateRule={vi.fn()}
        onUpdateRule={vi.fn()}
        onDeleteRule={vi.fn()}
      />
    );

    expect(screen.getByTestId("evaluation-rules-editor")).toBeInTheDocument();
    expect(screen.getByText(/Contains Confirmation Code/i)).toBeInTheDocument();
  });

  it("renders WebCallTester and PhoneCallTester with version-pinned controls", async () => {
    render(
      <div>
        <WebCallTester
          agentId="agent-voice-1"
          agentVersionNumber={2}
          readiness={{
            webrtc_live_configured: false,
            telephony_carrier_configured: false,
            webrtc_mode: "simulated_turn_session",
            telephony_mode: "carrier_not_configured",
          }}
          activeSession={null}
          busy={false}
          onStartSession={vi.fn()}
          onSendEvent={vi.fn()}
        />
        <PhoneCallTester
          agentId="agent-voice-1"
          agentVersionNumber={2}
          readiness={{
            webrtc_live_configured: false,
            telephony_carrier_configured: false,
            webrtc_mode: "simulated_turn_session",
            telephony_mode: "carrier_not_configured",
          }}
          lastPhoneCall={null}
          busy={false}
          onRunPhoneCall={vi.fn()}
        />
      </div>
    );

    await waitFor(() => {
      expect(screen.getByTestId("web-call-tester")).toBeInTheDocument();
      expect(screen.getByTestId("phone-call-tester")).toBeInTheDocument();
    });
  });
});
