/**
 * dashboard/src/tests/audit-console.test.tsx
 *
 * The audit page has no server-side CSV route, so export is built from the rows
 * already loaded and the button says how many rows that is. The 366-day window
 * cap and the UUID-typed actor filter are validated before the request so the
 * operator sees an explanation instead of a 422.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '../api/client';

const listAuditEvents = vi.fn();

vi.mock('../api/audit', async () => {
  const actual = await vi.importActual<typeof import('../api/audit')>('../api/audit');
  return { ...actual, listAuditEvents: (...args: unknown[]) => listAuditEvents(...args) };
});

import { AuditLogPage } from '../pages/audit/AuditLogPage';
import { AUDIT_RESULTS, exportEventsCsv, validateWindow } from '../api/audit';

const EVENT = {
  id: '3f7c1a52-1111-4a2b-9c33-1234567890ab',
  action: 'user.role_changed',
  event_type: 'user.role_changed',
  actor_id: '9a1b2c3d-1111-4a2b-9c33-abcdefabcdef',
  actor_type: 'user',
  actor_email: 'owner@example.com',
  target_user_id: '11112222-3333-4444-5555-666677778888',
  environment_id: null,
  resource_type: 'user',
  resource_id: '11112222-3333-4444-5555-666677778888',
  request_id: 'req_abc',
  result: 'success',
  ip_address: '203.0.113.7',
  user_agent: 'Mozilla/5.0',
  detail: { from: 'agent', to: 'manager' },
  created_at: '2026-10-20T08:00:00Z',
};

beforeEach(() => {
  listAuditEvents.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AuditLogPage', () => {
  it('renders events and the server-provided event-type catalogue', async () => {
    listAuditEvents.mockResolvedValue({
      items: [EVENT],
      total: 1,
      limit: 50,
      offset: 0,
      has_more: false,
      event_types: ['user.role_changed', 'call.completed'],
    });

    render(<AuditLogPage />);

    await waitFor(() => expect(screen.getByTestId(`audit-row-${EVENT.id}`)).toBeInTheDocument());
    expect(screen.getByTestId('audit-row-' + EVENT.id)).toHaveTextContent('owner@example.com');
    const eventTypeSelect = screen.getByTestId('audit-event-type');
    expect(Array.from(eventTypeSelect.querySelectorAll('option')).map((o) => o.value)).toContain(
      'user.role_changed',
    );
    expect(screen.getByText(/Showing 1 of 1/)).toBeInTheDocument();
  });

  it('renders a genuine empty result rather than an error', async () => {
    listAuditEvents.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      has_more: false,
      event_types: [],
    });

    render(<AuditLogPage />);

    await waitFor(() => expect(screen.getByText('No events match')).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('rejects an over-long window before calling the API', async () => {
    listAuditEvents.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      has_more: false,
      event_types: [],
    });

    render(<AuditLogPage />);
    await waitFor(() => expect(screen.getByTestId('audit-apply')).toBeInTheDocument());
    const callsBefore = listAuditEvents.mock.calls.length;

    await userEvent.type(screen.getByTestId('audit-start'), '2024-01-01T00:00');
    await userEvent.type(screen.getByTestId('audit-end'), '2026-10-01T00:00');
    await userEvent.click(screen.getByTestId('audit-apply'));

    await waitFor(() => expect(screen.getByTestId('audit-form-error')).toHaveTextContent(/366/));
    expect(listAuditEvents.mock.calls.length).toBe(callsBefore);
  });

  it('rejects an actor filter that is not a UUID', async () => {
    listAuditEvents.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      has_more: false,
      event_types: [],
    });

    render(<AuditLogPage />);
    await waitFor(() => expect(screen.getByTestId('audit-apply')).toBeInTheDocument());

    const actorInput = screen.getByPlaceholderText('user, call, campaign…').closest('label')?.parentElement;
    // The actor field is the only UUID-typed input on the form; locate it by its label.
    const uuidInput = screen.getByLabelText(/Actor id \(UUID\)/);
    expect(actorInput || uuidInput).toBeTruthy();
    await userEvent.type(uuidInput, 'not-a-uuid');
    await userEvent.click(screen.getByTestId('audit-apply'));

    await waitFor(() => expect(screen.getByTestId('audit-form-error')).toHaveTextContent(/UUID/));
  });

  it('exports exactly the loaded rows as RFC 4180 CSV', async () => {
    listAuditEvents.mockResolvedValue({
      items: [EVENT],
      total: 40,
      limit: 50,
      offset: 0,
      has_more: true,
      event_types: [],
    });

    const createObjectURL = vi.fn(() => 'blob:audit');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });

    render(<AuditLogPage />);
    await waitFor(() => expect(screen.getByTestId('audit-export')).toHaveTextContent('Export 1 row'));

    await userEvent.click(screen.getByTestId('audit-export'));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text.split('\r\n')[0]).toContain('created_at');
    expect(text).toContain('owner@example.com');
    // Only the loaded page is exported, never the 40 rows the server still holds.
    expect(text.trim().split('\r\n')).toHaveLength(2);
  });

  it('explains a 403 on the audit endpoint', async () => {
    listAuditEvents.mockRejectedValue(new ApiError('FORBIDDEN', 403, 'Forbidden'));

    render(<AuditLogPage />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/permission/i));
  });
});

describe('audit helpers', () => {
  it('accepts a window inside the 366-day cap and rejects one outside it', () => {
    expect(validateWindow(null, null)).toBeNull();
    expect(validateWindow('2026-01-01T00:00:00Z', '2026-12-31T00:00:00Z')).toBeNull();
    expect(validateWindow('2024-01-01T00:00:00Z', '2026-01-01T00:00:00Z')).toMatch(/366/);
    expect(validateWindow('2026-02-01T00:00:00Z', '2026-01-01T00:00:00Z')).toMatch(/earlier/);
  });

  it('quotes fields that contain commas or quotes', () => {
    const csv = exportEventsCsv([
      { ...EVENT, action: 'note, with comma', user_agent: 'agent says "hi"' },
    ]);
    expect(csv).toContain('"note, with comma"');
    expect(csv).toContain('"agent says ""hi"""');
    expect(AUDIT_RESULTS).toContain('denied');
  });
});
