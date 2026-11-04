/**
 * dashboard/src/tests/webhooks-console.test.tsx
 *
 * Delivery hygiene is the point of this page: a successful delivery must not be
 * replayable (the backend refuses it), a rotated secret is displayed exactly once
 * and never re-read, and the DLQ panel is only offered for a selected endpoint.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '../api/client';

const listWebhooks = vi.fn();
const listDeliveries = vi.fn();
const createWebhook = vi.fn();
const deleteWebhook = vi.fn();
const enableWebhook = vi.fn();
const disableWebhook = vi.fn();
const rotateSecret = vi.fn();
const sendTestEvent = vi.fn();
const retryDelivery = vi.fn();
const replayAllDlq = vi.fn();

vi.mock('../api/webhooks', () => ({
  listWebhooks: (...args: unknown[]) => listWebhooks(...args),
  listDeliveries: (...args: unknown[]) => listDeliveries(...args),
  createWebhook: (...args: unknown[]) => createWebhook(...args),
  deleteWebhook: (...args: unknown[]) => deleteWebhook(...args),
  enableWebhook: (...args: unknown[]) => enableWebhook(...args),
  disableWebhook: (...args: unknown[]) => disableWebhook(...args),
  rotateSecret: (...args: unknown[]) => rotateSecret(...args),
  sendTestEvent: (...args: unknown[]) => sendTestEvent(...args),
  retryDelivery: (...args: unknown[]) => retryDelivery(...args),
  replayAllDlq: (...args: unknown[]) => replayAllDlq(...args),
  getWebhook: vi.fn(),
  updateWebhook: vi.fn(),
  getDelivery: vi.fn(),
  replayWebhook: vi.fn(),
  listDlq: vi.fn(),
  webhookHealth: vi.fn(),
  webhookEventCategories: vi.fn(),
  allowedEvents: vi.fn(),
}));

import { WebhooksPage } from '../pages/webhooks/WebhooksPage';

const ENDPOINT = {
  id: 'wh-001',
  tenant_id: 'tenant-001',
  environment_id: null,
  url: 'https://hooks.example.com/voxdesk',
  description: 'Primary receiver',
  events: ['call.completed'],
  is_active: true,
  retry_policy: { max_attempts: 5, initial_delay_seconds: 30 },
  timeout_seconds: 15,
  headers: '***',
  secret_version: 2,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
};

const DELIVERY = {
  id: 'dl-00000001',
  endpoint_id: 'wh-001',
  event_id: 'call.completed:call-1',
  status: 'succeeded',
  http_status: 200,
  attempts: 1,
  attempt_limit: 5,
  next_retry_at: null,
  created_at: '2026-10-02T10:00:00Z',
  delivered_at: '2026-10-02T10:00:01Z',
  latency_ms: 120,
  last_error_category: null,
  is_dlq: false,
  replay_count: 0,
};

beforeEach(() => {
  for (const fn of [
    listWebhooks,
    listDeliveries,
    createWebhook,
    deleteWebhook,
    enableWebhook,
    disableWebhook,
    rotateSecret,
    sendTestEvent,
    retryDelivery,
    replayAllDlq,
  ]) {
    fn.mockReset();
  }
});

describe('WebhooksPage', () => {
  it('lists endpoints and the delivery log of the selected endpoint', async () => {
    listWebhooks.mockResolvedValue({ endpoints: [ENDPOINT], total: 1, limit: 100, offset: 0 });
    listDeliveries.mockResolvedValue({ deliveries: [DELIVERY], total: 1, limit: 50, offset: 0 });

    render(<WebhooksPage />);

    await waitFor(() => expect(screen.getByTestId('webhook-row-wh-001')).toHaveTextContent('hooks.example.com'));
    await waitFor(() => expect(screen.getByTestId('delivery-row-dl-00000001')).toHaveTextContent('200'));
    expect(screen.getByText('1 delivery record(s)')).toBeInTheDocument();
  });

  it('refuses to offer replay for a delivery that already succeeded', async () => {
    listWebhooks.mockResolvedValue({ endpoints: [ENDPOINT], total: 1, limit: 100, offset: 0 });
    listDeliveries.mockResolvedValue({ deliveries: [DELIVERY], total: 1, limit: 50, offset: 0 });

    render(<WebhooksPage />);

    await waitFor(() => expect(screen.getByTestId('replay-dl-00000001')).toBeInTheDocument());
    expect(screen.getByTestId('replay-dl-00000001')).toBeDisabled();
    expect(screen.getByTestId('replay-dl-00000001')).toHaveAttribute(
      'title',
      'Acknowledged deliveries are not resent',
    );
  });

  it('lets an operator replay a delivery that did not succeed', async () => {
    const failed = { ...DELIVERY, id: 'dl-00000002', status: 'retrying', http_status: 500, is_dlq: true };
    listWebhooks.mockResolvedValue({ endpoints: [ENDPOINT], total: 1, limit: 100, offset: 0 });
    listDeliveries.mockResolvedValue({ deliveries: [failed], total: 1, limit: 50, offset: 0 });
    retryDelivery.mockResolvedValue({ ...failed, status: 'pending' });

    render(<WebhooksPage />);

    await waitFor(() => expect(screen.getByTestId('replay-dl-00000002')).not.toBeDisabled());
    await userEvent.click(screen.getByTestId('replay-dl-00000002'));

    await waitFor(() => expect(retryDelivery).toHaveBeenCalledWith('wh-001', 'dl-00000002'));
    await waitFor(() => expect(screen.getByTestId('webhooks-notice')).toHaveTextContent('re-queued'));
  });

  it('shows a rotated signing secret exactly once', async () => {
    listWebhooks.mockResolvedValue({ endpoints: [ENDPOINT], total: 1, limit: 100, offset: 0 });
    listDeliveries.mockResolvedValue({ deliveries: [], total: 0, limit: 50, offset: 0 });
    rotateSecret.mockResolvedValue({ id: 'wh-001', secret_version: 3, secret: 'whsec_onetime' });

    render(<WebhooksPage />);

    await waitFor(() => expect(screen.getByTestId('rotate-wh-001')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('rotate-wh-001'));

    await waitFor(() => expect(screen.getByTestId('webhook-rotated-secret')).toHaveTextContent('whsec_onetime'));
    expect(screen.getByTestId('webhook-rotated-secret')).toHaveTextContent('version 3');

    await userEvent.click(screen.getByText('Dismiss'));
    expect(screen.queryByTestId('webhook-rotated-secret')).not.toBeInTheDocument();
  });

  it('asks for an endpoint URL through the create form and surfaces server validation', async () => {
    listWebhooks.mockResolvedValue({ endpoints: [], total: 0, limit: 100, offset: 0 });
    listDeliveries.mockResolvedValue({ deliveries: [], total: 0, limit: 50, offset: 0 });
    createWebhook.mockRejectedValue(new ApiError('VALIDATION_ERROR', 422, 'url must be a public https URL'));

    render(<WebhooksPage />);

    await waitFor(() => expect(screen.getByText('No endpoints')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('webhook-url'), 'https://hooks.example.com/new');
    await userEvent.click(screen.getByTestId('webhook-create'));

    await waitFor(() => expect(createWebhook).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/public https URL/));
  });

  it('explains a 401 without hiding the rest of the console shell', async () => {
    listWebhooks.mockRejectedValue(new ApiError('UNAUTHORIZED', 401, 'Not authenticated'));
    listDeliveries.mockResolvedValue({ deliveries: [], total: 0, limit: 50, offset: 0 });

    render(<WebhooksPage />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/sign in/i));
  });
});
