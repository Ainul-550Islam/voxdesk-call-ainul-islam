/**
 * dashboard/src/__tests__/prompt6.telephony.test.tsx
 * Vitest unit & component tests for Prompt 6 — Telephony / Voice Runtime UI:
 * PhoneNumbersPage, CallRuntimePage, PhoneNumberTable, SipConnectionDialog, and CallControlPanel.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as telephonyApi from '../lib/telephonyApi';
import * as agentsApi from '../api/agents';
import { PhoneNumbersPage } from '../pages/PhoneNumbersPage';
import { CallRuntimePage } from '../pages/CallRuntimePage';

vi.mock('../lib/telephonyApi');
vi.mock('../api/agents');

const MOCK_AGENTS = [
  {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'Inbound Receptionist Agent',
    status: 'published',
  },
  {
    id: '22222222-2222-2222-2222-222222222222',
    name: 'Escalation Specialist Agent',
    status: 'published',
  },
];

const MOCK_PHONE_NUMBER: telephonyApi.PhoneNumberRecord = {
  id: 'aaaa1111-1111-1111-1111-111111111111',
  organization_id: 'org-001',
  environment_id: null,
  number: '+14155550101',
  e164_number: '+14155550101',
  provider: 'TWILIO',
  provider_number_id: 'PN_twilio_001',
  sip_connection_id: null,
  sip_enabled: false,
  inbound_enabled: true,
  outbound_enabled: true,
  inbound_agent_id: '11111111-1111-1111-1111-111111111111',
  outbound_agent_id: '11111111-1111-1111-1111-111111111111',
  status: 'READY',
  metadata: {},
  last_health_check_at: '2026-10-04T05:00:00Z',
  last_health_error: null,
  created_at: '2026-10-04T05:00:00Z',
  updated_at: '2026-10-04T05:00:00Z',
};

const MOCK_SIP_CONNECTION: telephonyApi.SipConnectionRecord = {
  id: 'bbbb2222-2222-2222-2222-222222222222',
  organization_id: 'org-001',
  environment_id: null,
  name: 'Primary Carrier TLS Trunk',
  provider: 'SIP',
  phone_number_e164: '+14155550101',
  termination_uri: 'sip:pstn.carrier.example.com:5061',
  origination_uri: 'sip:ingress.voxdesk.example.com:5061',
  username: 'sip_user',
  credential_reference: 'sec_ref_sha256_abcdef1234567890',
  has_credentials: true,
  transport: 'TLS',
  status: 'READY',
  last_test_at: '2026-10-04T05:05:00Z',
  last_error: null,
  metadata: {},
  created_at: '2026-10-04T05:00:00Z',
  updated_at: '2026-10-04T05:05:00Z',
};

const MOCK_CALL_SESSION: telephonyApi.CallSessionRecord = {
  id: 'cccc3333-3333-3333-3333-333333333333',
  organization_id: 'org-001',
  environment_id: null,
  legacy_call_id: null,
  phone_number_id: MOCK_PHONE_NUMBER.id,
  agent_id: MOCK_AGENTS[0].id,
  agent_version_number: 1,
  provider: 'SIMULATED',
  provider_call_id: 'sim_call_001',
  direction: 'INBOUND',
  from_number: '+14155559999',
  to_number: '+14155550101',
  status: 'IN_PROGRESS',
  media_state: 'STREAMING',
  transfer_state: 'NONE',
  transfer_mode: null,
  transfer_target: null,
  transferred_to_agent_id: null,
  parent_call_id: null,
  is_simulation: true,
  execution_kind: 'SIMULATION',
  started_at: '2026-10-04T05:10:00Z',
  answered_at: '2026-10-04T05:10:02Z',
  ended_at: null,
  duration_ms: 15000,
  billable_seconds: 15,
  usage_finalized: false,
  hangup_reason: null,
  recording_reference: null,
  transcript_reference: 'transcript://calls/cccc3333#turns=2',
  transcript_turns: [
    {
      turn_index: 0,
      role: 'agent',
      content: 'Hello! Thank you for calling VoxDesk.',
      timestamp: '2026-10-04T05:10:02Z',
    },
    {
      turn_index: 1,
      role: 'caller',
      content: 'I would like to speak with billing support.',
      timestamp: '2026-10-04T05:10:06Z',
    },
  ],
  dtmf_buffer: '3#',
  dtmf_events: [],
  runtime_events: [
    {
      event_id: 'rt_001',
      event_type: 'CALL_CREATED',
      state: 'CREATED',
      timestamp: '2026-10-04T05:10:00Z',
      detail: {},
    },
    {
      event_id: 'rt_002',
      event_type: 'CALL_STARTED',
      state: 'IN_PROGRESS',
      timestamp: '2026-10-04T05:10:02Z',
      detail: {},
    },
  ],
  metadata: {},
  created_at: '2026-10-04T05:10:00Z',
  updated_at: '2026-10-04T05:10:06Z',
};

describe('Prompt 6 — Telephony / Voice Runtime UI', () => {
  beforeEach(() => {
    vi.mocked(agentsApi.listAgents).mockResolvedValue({
      agents: MOCK_AGENTS as never[],
      total: 2,
    });
    vi.mocked(telephonyApi.listPhoneNumbers).mockResolvedValue({
      items: [MOCK_PHONE_NUMBER],
      total: 1,
    });
    vi.mocked(telephonyApi.listSipConnections).mockResolvedValue({
      items: [MOCK_SIP_CONNECTION],
      total: 1,
    });
    vi.mocked(telephonyApi.getTelephonyHealth).mockResolvedValue({
      state: 'READY',
      default_provider: 'SIMULATED',
      configured_phone_numbers: 1,
      ready_phone_numbers: 1,
      configured_sip_connections: 1,
      ready_sip_connections: 1,
      active_calls: 1,
      providers: [],
      checked_at: '2026-10-04T05:10:00Z',
    });
    vi.mocked(telephonyApi.getTelephonyUsage).mockResolvedValue({
      organization_id: 'org-001',
      call_count: 4,
      total_duration_ms: 180000,
      total_billable_seconds: 180,
      total_billable_minutes: 3,
    });
    vi.mocked(telephonyApi.listTelephonyCalls).mockResolvedValue({
      items: [MOCK_CALL_SESSION],
      total: 1,
    });
    vi.mocked(telephonyApi.getTelephonyCall).mockResolvedValue(MOCK_CALL_SESSION);
    vi.mocked(telephonyApi.sendTelephonyCallDtmf).mockResolvedValue({
      call_id: MOCK_CALL_SESSION.id,
      accepted_digits: '1',
      dtmf_buffer: '3#1',
      matched_route: { department: 'sales' },
      barge_in_triggered: false,
      status: 'IN_PROGRESS',
    });
    vi.mocked(telephonyApi.transferTelephonyCall).mockResolvedValue({
      id: 'xfer-001',
      call_session_id: MOCK_CALL_SESSION.id,
      organization_id: 'org-001',
      transfer_mode: 'COLD',
      source_agent_id: MOCK_AGENTS[0].id,
      target_agent_id: null,
      target_destination: '+14155550188',
      whisper_message: null,
      fallback_action: 'RETURN_TO_AGENT',
      status: 'COMPLETED',
      reason: 'customer_requested',
      failure_reason: null,
      context_snapshot: {},
      requested_at: '2026-10-04T05:11:00Z',
      completed_at: '2026-10-04T05:11:01Z',
    });
    vi.mocked(telephonyApi.hangupTelephonyCall).mockResolvedValue({
      ...MOCK_CALL_SESSION,
      status: 'COMPLETED',
      usage_finalized: true,
    });
  });

  it('renders PhoneNumbersPage with E.164 numbers, agent bindings, and SIP trunks', async () => {
    render(<PhoneNumbersPage />);

    await waitFor(() => {
      expect(screen.getByTestId('phone-numbers-page')).toBeInTheDocument();
      expect(screen.getByTestId('phone-row-+14155550101')).toBeInTheDocument();
    });

    expect(screen.getByTestId('telephony-health-badge')).toHaveTextContent('READY');
    expect(screen.getByTestId('sip-connection-dialog')).toHaveTextContent(
      'Primary Carrier TLS Trunk'
    );
  });

  it('renders CallRuntimePage with active call session, DTMF keypad, transfer, and hangup controls', async () => {
    render(<CallRuntimePage />);

    await waitFor(() => {
      expect(screen.getByTestId('call-runtime-page')).toBeInTheDocument();
      expect(screen.getByTestId('call-control-panel')).toBeInTheDocument();
    });

    expect(screen.getByTestId('call-session-status-badge')).toHaveTextContent(
      'IN_PROGRESS'
    );
    expect(screen.getByText(/Hello! Thank you for calling VoxDesk/i)).toBeInTheDocument();

    // Send DTMF digit '1'
    fireEvent.click(screen.getByTestId('dtmf-key-1'));
    await waitFor(() => {
      expect(telephonyApi.sendTelephonyCallDtmf).toHaveBeenCalledWith(
        MOCK_CALL_SESSION.id,
        { digits: '1', source: 'operator_console' }
      );
    });

    // Execute Transfer
    fireEvent.click(screen.getByTestId('execute-transfer-btn'));
    await waitFor(() => {
      expect(telephonyApi.transferTelephonyCall).toHaveBeenCalled();
    });

    // Hangup Call
    fireEvent.click(screen.getByTestId('hangup-call-btn'));
    await waitFor(() => {
      expect(telephonyApi.hangupTelephonyCall).toHaveBeenCalledWith(
        MOCK_CALL_SESSION.id,
        'operator_hangup'
      );
    });
  });
});
