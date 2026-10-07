import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { isProtectedPath, matchRoute } from '../app/router';
import { WorkflowsPage } from '../pages/WorkflowsPage';
import { FinalParityPage } from '../pages/FinalParityPage';
import { getCapabilityInventory, getIntegrationInventory, inspectE2EFlow } from '../lib/parityApi';

const WORKFLOW_ID = 'workflow-prompt8-001';
const LEAD_ID = '11111111-2222-4333-8444-555555555555';

interface WorkflowFixture {
  id: string;
  tenant_id: string;
  name: string;
  version: number;
  status: string;
  trigger: string;
  entry_node: string;
  description: string;
  nodes: Array<{ id: string; type: string; next: string; delay_seconds: number; retry_limit: number }>;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Prompt 8 direct routes and workflow UI', () => {
  it('renders live parity API evidence without upgrading route presence to verification', async () => {
    const capabilityInventory = {
      generated_at: '2026-10-06T00:00:00Z',
      registered_api_operations: 1617,
      suppressed_generated_placeholder_routes: 38,
      suppressed_generic_placeholder_routes: 0,
      capabilities: [
        {
          key: 'voice_agents',
          label: 'Voice agents and versioned builder',
          status: 'PARTIAL',
          summary: 'Builder routes exist; provider reachability is separate.',
          evidence_routes: [
            { method: 'GET', path: '/api/v1/agents', module: 'app.api.v1.agent_routes' },
          ],
          evidence_basis: 'registered_routes_only',
        },
      ],
      limitation: 'Route registration is implementation evidence only; it is not an E2E pass.',
    };
    const integrationInventory = {
      tenant_id: 'tenant-prompt8-001',
      generated_at: '2026-10-06T00:00:00Z',
      items: [
        {
          integration_type: 'telephony',
          provider: 'twilio',
          status: 'NOT_CONFIGURED',
          configured: false,
          enabled: null,
          credentials_present: false,
          last_health_check_at: null,
          last_health_ok: null,
          status_basis: 'Required provider credentials are absent.',
        },
      ],
      limitation: 'Provider credentials are not returned.',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/parity/capabilities') return jsonResponse(capabilityInventory);
      if (url === '/api/v1/parity/integrations') return jsonResponse(integrationInventory);
      return jsonResponse({ detail: `Unexpected test request: ${url}` }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<FinalParityPage />);

    expect(await screen.findByText('1,617')).toBeInTheDocument();
    expect(screen.getByText(/0 implemented · 1 partial/)).toBeInTheDocument();
    expect(screen.getByText('NOT_CONFIGURED')).toBeInTheDocument();
    expect(screen.queryByText('PRODUCTION_READY')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([input]) => String(input)).sort()).toEqual([
      '/api/v1/parity/capabilities',
      '/api/v1/parity/integrations',
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer prompt8-ui-test-token');
    }
  });
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('voxdesk_access_token', 'prompt8-ui-test-token');
  });

  it.each([
    ['/campaigns', 'LegacyCampaignsPage'],
    ['/dashboard/campaigns', 'LegacyCampaignsPage'],
    ['/workflows', 'WorkflowsPage'],
    ['/dashboard/workflows', 'WorkflowsPage'],
    ['/settings', 'LegacySettingsPage'],
    ['/dashboard/settings', 'LegacySettingsPage'],
    ['/billing', 'LegacyBillingPage'],
    ['/dashboard/billing', 'LegacyBillingPage'],
  ])('maps %s to a real protected page', (path, component) => {
    const match = matchRoute(path);
    expect(match?.route.component).toBe(component);
    expect(isProtectedPath(path)).toBe(true);
  });

  it('builds authenticated parity URLs with one API prefix and explicit scope parameters', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    await getCapabilityInventory();
    await getIntegrationInventory();
    await inspectE2EFlow('agent-001', 'call-002', 'environment-003');

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/v1/parity/capabilities',
      '/api/v1/parity/integrations',
      '/api/v1/parity/e2e/inspect?agent_id=agent-001&call_id=call-002&environment_id=environment-003',
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer prompt8-ui-test-token');
    }
  });

  it('creates, publishes, executes, and reloads persisted workflow API records', async () => {
    const user = userEvent.setup();
    let persistedWorkflows: WorkflowFixture[] = [];
    const workflowPayloads: unknown[] = [];
    const executionPayloads: unknown[] = [];
    const executionKeys: string[] = [];
    const execution = {
      id: 'execution-prompt8-001',
      workflow_id: WORKFLOW_ID,
      tenant_id: 'tenant-prompt8-001',
      idempotency_key: 'server-persisted-key',
      status: 'completed',
      current_node: 'complete',
      steps: [
        { node_id: 'qualify_lead', status: 'completed', detail: 'Lead status updated.', attempt: 1, at: '2026-10-05T00:00:00Z' },
        { node_id: 'complete', status: 'completed', detail: 'Workflow completed.', attempt: 1, at: '2026-10-05T00:00:01Z' },
      ],
      started_at: '2026-10-05T00:00:00Z',
      finished_at: '2026-10-05T00:00:01Z',
      error: '',
    };

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || 'GET').toUpperCase();
      if (url === '/api/workflows' && method === 'GET') {
        return jsonResponse(persistedWorkflows);
      }
      if (url === '/api/workflows' && method === 'POST') {
        const payload = JSON.parse(String(init?.body || '{}')) as Record<string, unknown>;
        workflowPayloads.push(payload);
        const created: WorkflowFixture = {
          id: WORKFLOW_ID,
          tenant_id: 'tenant-prompt8-001',
          name: String(payload.name),
          version: 1,
          status: 'draft',
          trigger: String(payload.trigger),
          entry_node: String(payload.entry_node),
          description: String(payload.description),
          nodes: [
            { id: 'qualify_lead', type: 'action', next: 'complete', delay_seconds: 0, retry_limit: 3 },
            { id: 'complete', type: 'terminal', next: '', delay_seconds: 0, retry_limit: 3 },
          ],
        };
        persistedWorkflows = [created];
        return jsonResponse(created, 201);
      }
      if (url === `/api/workflows/${WORKFLOW_ID}/publish` && method === 'POST') {
        persistedWorkflows = persistedWorkflows.map((workflow) => ({ ...workflow, status: 'active' }));
        return jsonResponse(persistedWorkflows[0]);
      }
      if (url === `/api/workflows/${WORKFLOW_ID}/execute` && method === 'POST') {
        executionPayloads.push(JSON.parse(String(init?.body || '{}')));
        executionKeys.push(new Headers(init?.headers).get('Idempotency-Key') || '');
        return jsonResponse(execution);
      }
      if (url === `/api/workflows/${WORKFLOW_ID}/executions` && method === 'GET') {
        return jsonResponse([execution]);
      }
      return jsonResponse({ detail: `Unexpected test request: ${method} ${url}` }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<WorkflowsPage />);
    expect(await screen.findByText('No workflows are configured in this workspace.')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Workflow name'), 'Prompt 8 workflow UI test');
    await user.click(screen.getByRole('button', { name: 'Create draft' }));
    expect(await screen.findByRole('heading', { name: 'Prompt 8 workflow UI test' })).toBeInTheDocument();
    expect(workflowPayloads).toHaveLength(1);
    expect(workflowPayloads[0]).toMatchObject({
      trigger: 'manual',
      entry_node: 'qualify_lead',
      nodes: [
        { id: 'qualify_lead', type: 'action', action_name: 'update_lead_status' },
        { id: 'complete', type: 'terminal' },
      ],
    });

    await user.click(screen.getByRole('button', { name: 'Publish workflow' }));
    await waitFor(() => expect(screen.getByText(/ACTIVE · v1/)).toBeInTheDocument());

    await user.type(screen.getByLabelText('Existing lead UUID for execution'), LEAD_ID);
    await user.click(screen.getByRole('button', { name: 'Execute for lead' }));
    expect(await screen.findByText(/Persisted executions \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Execution execution-prompt8-001 completed with status/)).toBeInTheDocument();
    expect(executionPayloads).toEqual([{ payload: { lead_id: LEAD_ID } }]);
    expect(executionKeys).toHaveLength(1);
    expect(executionKeys[0]).toMatch(/^workflow-ui-/);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/workflows',
      expect.objectContaining({ method: 'GET' }),
    ));
  });
});
