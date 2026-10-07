import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ConductorPage } from '../pages/conductor/ConductorPage';

const mockAgentsApi = vi.hoisted(() => ({
  listAgents: vi.fn(),
}));

vi.mock('../api/agents', () => mockAgentsApi);

const mockProposalReady = {
  id: 'prop-100',
  session_id: 'sess-100',
  tenant_id: 'tenant-1',
  environment_id: null,
  agent_id: 'agent-clinic-1',
  agent_kind: 'voice' as const,
  base_agent_version_id: 'ver-1',
  base_version_number: 1,
  base_config_hash: 'aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666aaaa1111bbbb2222',
  base_draft_etag: 'etag-1',
  base_config_snapshot: {
    name: 'Dhaka Clinic Agent',
    greeting: 'Hello!',
    voice_speed: 1.0,
  },
  request_text: 'Change greeting to Welcome to Dhaka Clinic and slow down voice speed to 0.92',
  summary: 'Conductor proposed 2 configuration change(s)',
  rationale: 'Synthesized 2 structured operation(s)',
  status: 'READY_FOR_REVIEW' as const,
  validation_status: 'valid',
  validation_report: { valid: true, status: 'valid', errors: [], warnings: [] },
  simulation_status: 'passed',
  simulation_summary: {
    test_run_id: 'run-500',
    test_case_id: 'case-500',
    scorecard_summary: { status: 'PASSED', overall_score: 100 },
  },
  risk_summary: { overall_risk: 'low' as const, total_changes: 2 },
  candidate_config_snapshot: {
    name: 'Dhaka Clinic Agent',
    greeting: 'Welcome to Dhaka Clinic',
    voice_speed: 0.92,
  },
  final_candidate_hash: '1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff',
  resulting_agent_version_id: null,
  resulting_version_number: null,
  apply_idempotency_key: null,
  approved_change_hash: null,
  is_mock_provider: true,
  provider: 'deterministic_sandbox',
  model: 'conductor-rule-synthesizer-v1',
  correlation_id: 'corr-1',
  error_code: null,
  error_message: null,
  production_published: false,
  ready_to_publish: false,
  changes: [
    {
      id: 'chg-1',
      proposal_id: 'prop-100',
      tenant_id: 'tenant-1',
      sequence: 0,
      section: 'greeting',
      path: 'greeting',
      operation: 'replace' as const,
      old_value: 'Hello!',
      new_value: 'Welcome to Dhaka Clinic',
      reason: 'Update greeting as requested',
      evidence_ids: ['ev-1'],
      risk_level: 'low' as const,
      validation_state: 'valid',
      validation_messages: [],
      simulation_state: 'passed',
      approval_state: 'pending' as const,
      applied_at: null,
      created_at: '2026-10-03T10:00:00Z',
      updated_at: '2026-10-03T10:00:00Z',
    },
    {
      id: 'chg-2',
      proposal_id: 'prop-100',
      tenant_id: 'tenant-1',
      sequence: 1,
      section: 'voice',
      path: 'voice_speed',
      operation: 'set' as const,
      old_value: 1.0,
      new_value: 0.92,
      reason: 'Adjust speaking rate to 0.92x',
      evidence_ids: ['ev-1'],
      risk_level: 'low' as const,
      validation_state: 'valid',
      validation_messages: [],
      simulation_state: 'passed',
      approval_state: 'pending' as const,
      applied_at: null,
      created_at: '2026-10-03T10:00:00Z',
      updated_at: '2026-10-03T10:00:00Z',
    },
  ],
  evidence: [
    {
      id: 'ev-1',
      proposal_id: 'prop-100',
      tenant_id: 'tenant-1',
      source_type: 'agent_version',
      source_id: 'agent-clinic-1:v1',
      evidence_summary: 'Pinned baseline configuration snapshot from v1.',
      evidence_payload: { version_number: 1 },
      created_at: '2026-10-03T10:00:00Z',
    },
  ],
  approvals: [],
  created_by: 'user-1',
  created_at: '2026-10-03T10:00:00Z',
  updated_at: '2026-10-03T10:00:00Z',
  applied_at: null,
};

const mockDiff = {
  proposal_id: 'prop-100',
  agent_id: 'agent-clinic-1',
  base_version_number: 1,
  base_config_hash: 'aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666aaaa1111bbbb2222',
  candidate_config_hash: '1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff',
  approved_candidate_config_hash: '1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff',
  diff_hash: '9999888877776666555544443333222211110000ffffeeeeddddccccbbbbaaaa',
  total_changes: 2,
  approved_changes: 0,
  rejected_changes: 0,
  pending_changes: 2,
  grouped_by_section: {
    greeting: [
      {
        change_id: 'chg-1',
        sequence: 0,
        section: 'greeting',
        json_pointer: '/greeting',
        path: 'greeting',
        operation: 'replace' as const,
        old_value: 'Hello!',
        new_value: 'Welcome to Dhaka Clinic',
        reason: 'Update greeting as requested',
        risk_level: 'low' as const,
        approval_state: 'pending' as const,
        validation_state: 'valid',
        simulation_state: 'passed',
        is_text_diff: true,
        line_diff: ['- Hello!', '+ Welcome to Dhaka Clinic'],
      },
    ],
  },
  changes: [
    {
      change_id: 'chg-1',
      sequence: 0,
      section: 'greeting',
      json_pointer: '/greeting',
      path: 'greeting',
      operation: 'replace' as const,
      old_value: 'Hello!',
      new_value: 'Welcome to Dhaka Clinic',
      reason: 'Update greeting as requested',
      risk_level: 'low' as const,
      approval_state: 'pending' as const,
      validation_state: 'valid',
      simulation_state: 'passed',
      is_text_diff: true,
      line_diff: ['- Hello!', '+ Welcome to Dhaka Clinic'],
    },
  ],
  side_by_side: {
    base_config: { greeting: 'Hello!', voice_speed: 1.0 },
    candidate_config: { greeting: 'Welcome to Dhaka Clinic', voice_speed: 0.92 },
    approved_candidate_config: { greeting: 'Welcome to Dhaka Clinic', voice_speed: 0.92 },
  },
};

const mockConductorApi = vi.hoisted(() => ({
  listConductorSessions: vi.fn().mockResolvedValue([]),
  listConductorProposals: vi.fn().mockResolvedValue([]),
  createConductorSession: vi.fn(),
  getConductorSession: vi.fn(),
  getConductorProposal: vi.fn(),
  getProposalDiff: vi.fn(),
  submitConductorRequest: vi.fn(),
  validateProposal: vi.fn(),
  simulateProposal: vi.fn(),
  approveChange: vi.fn(),
  rejectChange: vi.fn(),
  undoChangeApproval: vi.fn(),
  approveProposal: vi.fn(),
  rejectProposal: vi.fn(),
  undoProposalApproval: vi.fn(),
  applyProposal: vi.fn(),
}));

vi.mock('../api/conductor', () => mockConductorApi);

describe('ConductorPage & ConductorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAgentsApi.listAgents.mockResolvedValue({
      agents: [
        {
          id: 'agent-clinic-1',
          name: 'Dhaka Clinic Agent',
          active_version_number: 1,
          status: 'active',
        },
      ],
      total: 1,
    });
    mockConductorApi.listConductorSessions.mockResolvedValue([]);
    mockConductorApi.listConductorProposals.mockResolvedValue([]);
  });

  it('renders empty state with no fake/mock proposals on initial load', async () => {
    render(<ConductorPage />);

    expect(
      await screen.findByText(/Conductor Studio — AI Agent Build, Review, Test & Safe Apply/i)
    ).toBeTruthy();

    expect(
      await screen.findByTestId('conductor-no-proposal-state')
    ).toBeTruthy();
  });

  it('submits a natural-language request, approves changes, and creates a new immutable AgentVersion without auto-publishing to production', async () => {
    mockConductorApi.submitConductorRequest.mockResolvedValue(mockProposalReady);
    mockConductorApi.getProposalDiff.mockResolvedValue(mockDiff);

    const approvedProposal = {
      ...mockProposalReady,
      status: 'APPROVED' as const,
      changes: mockProposalReady.changes.map((c) => ({
        ...c,
        approval_state: 'approved' as const,
      })),
    };
    mockConductorApi.approveProposal.mockResolvedValue(approvedProposal);
    mockConductorApi.getConductorProposal.mockResolvedValueOnce(approvedProposal);

    const appliedProposal = {
      ...approvedProposal,
      status: 'APPLIED' as const,
      resulting_version_number: 2,
      production_published: false,
      ready_to_publish: true,
    };
    mockConductorApi.applyProposal.mockResolvedValue({
      proposal: appliedProposal,
      resulting_version: {
        id: 'ver-2',
        agent_id: 'agent-clinic-1',
        version_number: 2,
        config_hash: appliedProposal.final_candidate_hash,
        config_snapshot: appliedProposal.candidate_config_snapshot,
        production_published: false,
        ready_to_publish: true,
      },
      applied_change_ids: ['chg-1', 'chg-2'],
      skipped_change_ids: [],
      base_version_number: 1,
      resulting_version_number: 2,
      base_config_hash: mockProposalReady.base_config_hash,
      resulting_config_hash: appliedProposal.final_candidate_hash,
      production_published: false,
      ready_to_publish: true,
      idempotent_replay: false,
    });
    mockConductorApi.getConductorProposal.mockResolvedValueOnce(appliedProposal);

    render(<ConductorPage />);

    const textarea = await screen.findByTestId('conductor-request-input');
    fireEvent.change(textarea, {
      target: {
        value:
          'Change greeting to Welcome to Dhaka Clinic and slow down voice speed to 0.92',
      },
    });

    const submitBtn = screen.getByTestId('submit-conductor-request-btn');
    fireEvent.click(submitBtn);

    expect(await screen.findByTestId('proposal-change-list')).toBeTruthy();
    expect(screen.getByTestId('conductor-provider-badge').textContent).toContain(
      'SANDBOX MOCK'
    );
    expect(screen.getByTestId('change-path-0').textContent).toBe('greeting');
    expect(screen.getByTestId('change-path-1').textContent).toBe('voice_speed');

    fireEvent.click(screen.getByTestId('accept-all-changes-btn'));
    await waitFor(() => {
      expect(mockConductorApi.approveProposal).toHaveBeenCalledWith(
        'prop-100',
        { reason: 'Accepted all proposed changes', safe_only: false }
      );
    });

    const applyBtn = await screen.findByTestId('apply-approved-proposal-btn');
    fireEvent.click(applyBtn);

    expect(await screen.findByTestId('applied-version-banner')).toBeTruthy();
    expect(screen.getByTestId('production-safety-notice').textContent).toContain(
      'Production was NOT auto-modified'
    );
  });
});
