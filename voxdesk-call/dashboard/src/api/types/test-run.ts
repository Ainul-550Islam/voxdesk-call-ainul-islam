/**
 * dashboard/src/api/types/test-run.ts
 * Canonical TypeScript test-suite, test-case, test-run, and batch status contracts matching durable API state.
 */

export type {
  AgentKind,
  BatchRunAggregationSummary,
  BatchSuiteRunPayload,
  CallTestReadiness,
  LLMPlaygroundRunPayload,
  MultiTurnSimulationPayload,
  PhoneCallTestPayload,
  SimulationEventEntry,
  SimulationTranscriptTurn,
  TestCase,
  TestCaseCreatePayload,
  TestCaseUpdatePayload,
  TestPassPolicy,
  TestRun,
  TestRunMode,
  TestRunStatus,
  TestSuite,
  TestSuiteCreatePayload,
  TestSuiteStatus,
  TestSuiteUpdatePayload,
  WebCallSessionEventPayload,
  WebCallSessionPayload,
} from '../../types/evaluation';
