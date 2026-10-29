/**
 * Tests for the live call-control contract: API client, hook and component.
 *
 * The point of these is that the UI must not claim a call is paused until the
 * server says so, and must not offer a control the provider cannot perform.
 * Every assertion is about real state — the request that was sent, the key it
 * carried, and what the component renders afterwards.
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import {
  cancelTransfer,
  pauseCall,
  resumeCall,
  sendDtmfBatch,
  whisperTransfer,
} from '../api/calls';
import { CallControlBar } from '../components/calls/CallControlBar';
import { useCallControls, toControlError } from '../hooks/useCallControls';

interface RecordedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

const recorded: RecordedRequest[] = [];
let nextResponse: { ok: boolean; status: number; body: unknown } = {
  ok: true,
  status: 200,
  body: {},
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  recorded.length = 0;
  nextResponse = { ok: true, status: 200, body: {} };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL, init?: RequestInit) => {
      const request: RecordedRequest = {
        url: String(url),
        method: (init?.method as string) ?? 'GET',
        headers: (init?.headers as Record<string, string>) ?? {},
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };
      recorded.push(request);
      return nextResponse.ok
        ? jsonResponse(nextResponse.status, nextResponse.body)
        : jsonResponse(nextResponse.status, nextResponse.body);
    }) as unknown as typeof fetch
  );
  vi.stubGlobal(
    'localStorage',
    {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    } as unknown as Storage
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: 0 }, queries: { retry: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  );
}

const CAPABILITIES = {
  pause: true,
  resume: true,
  dtmf: true,
  transfer_cancel: true,
  whisper: true,
  bulk_transfer: true,
};

describe('calls control API client', () => {
  it('sends an Idempotency-Key with every control action', async () => {
    nextResponse = { ok: true, status: 200, body: { call_id: 'c1' } };

    await pauseCall('call-1', { reason: 'hold' }, 'key-pause-1');
    expect(recorded[0].url).toContain('/api/calls/call-1/pause');
    expect(recorded[0].method).toBe('POST');
    expect(recorded[0].headers['Idempotency-Key']).toBe('key-pause-1');
    expect(recorded[0].body).toEqual({ reason: 'hold' });

    await resumeCall('call-1', { reason: 'back' }, 'key-resume-1');
    expect(recorded[1].url).toContain('/api/calls/call-1/resume');
    expect(recorded[1].headers['Idempotency-Key']).toBe('key-resume-1');

    await cancelTransfer('call-1', { reason: 'stop' }, 'key-cancel-1');
    expect(recorded[2].url).toContain('/api/calls/call-1/transfer/cancel');
    expect(recorded[2].headers['Idempotency-Key']).toBe('key-cancel-1');

    await whisperTransfer('call-1', 'heads up', 'key-whisper-1');
    expect(recorded[3].url).toContain('/api/calls/call-1/transfer/whisper');
    expect(recorded[3].headers['Idempotency-Key']).toBe('key-whisper-1');
    expect(recorded[3].body).toEqual({ message: 'heads up' });
  });

  it('generates a key per DTMF item so a failed run can be retried alone', async () => {
    nextResponse = { ok: true, status: 200, body: { items: [] } };

    await sendDtmfBatch(
      'call-1',
      [{ digits: '1' }, { digits: '2' }, { digits: '3#' }],
      'batch-key'
    );

    const body = recorded[0].body as { items: Array<{ digits: string; idempotency_key: string }> };
    expect(body.items).toHaveLength(3);
    const keys = body.items.map((item) => item.idempotency_key);
    expect(new Set(keys).size).toBe(3);
    expect(keys[0]).toBe('batch-key-0');
    expect(keys[2]).toBe('batch-key-2');
  });

  it('generates a key when the caller does not supply one', async () => {
    nextResponse = { ok: true, status: 200, body: {} };
    await pauseCall('call-1');
    const key = recorded[0].headers['Idempotency-Key'];
    expect(typeof key).toBe('string');
    expect(key.length).toBeGreaterThanOrEqual(8);
  });

  it('surfaces the server code and message for an unsupported capability', async () => {
    nextResponse = {
      ok: false,
      status: 501,
      body: { detail: { code: 'UNSUPPORTED_CAPABILITY', message: 'no hold' } },
    };
    await expect(pauseCall('call-1', {}, 'k')).rejects.toMatchObject({
      status: 501,
    });
    expect(toControlError({ status: 501, detail: { code: 'UNSUPPORTED_CAPABILITY', message: 'no hold' } })).toEqual(
      { code: 'UNSUPPORTED_CAPABILITY', message: 'no hold', status: 501 }
    );
  });
});

describe('useCallControls', () => {
  function Harness() {
    const controls = useCallControls({ callId: 'call-1' });
    return (
      <div>
        <button onClick={() => controls.pause.mutate({ reason: 'x' })}>go</button>
        <span data-testid="pending">{String(controls.pause.isPending)}</span>
        <span data-testid="error">{controls.pause.error?.code ?? 'none'}</span>
        <span data-testid="success">{String(controls.pause.isSuccess)}</span>
      </div>
    );
  }

  it('reports success only after the server responds', async () => {
    nextResponse = {
      ok: true,
      status: 200,
      body: { call_id: 'call-1', status: 'paused', capabilities: CAPABILITIES },
    };
    renderWithClient(<Harness />);
    await userEvent.click(screen.getByText('go'));
    await waitFor(() =>
      expect(screen.getByTestId('success').textContent).toBe('true')
    );
    expect(screen.getByTestId('error').textContent).toBe('none');
  });

  it('exposes the server error code on failure', async () => {
    nextResponse = {
      ok: false,
      status: 409,
      body: { detail: { code: 'call_already_paused', message: 'already paused' } },
    };
    renderWithClient(<Harness />);
    await userEvent.click(screen.getByText('go'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toBe('call_already_paused')
    );
    expect(screen.getByTestId('success').textContent).toBe('false');
  });
});

describe('CallControlBar', () => {
  it('disables pause and resume when the provider reports no hold support', () => {
    renderWithClient(
      <CallControlBar
        callId="call-1"
        status="in_progress"
        capabilities={{
          pause: false,
          resume: false,
          dtmf: true,
          transfer_cancel: false,
          whisper: true,
          bulk_transfer: true,
        }}
      />
    );
    expect(screen.getByTestId('call-control-pause')).toBeDisabled();
    expect(screen.getByTestId('call-control-resume')).toBeDisabled();
    expect(
      screen.getByTestId('call-control-pause').getAttribute('title')
    ).toContain('UNSUPPORTED_CAPABILITY');
  });

  it('disables every control for a call that is not live', () => {
    renderWithClient(
      <CallControlBar callId="call-1" status="completed" capabilities={CAPABILITIES} />
    );
    expect(screen.getByTestId('call-control-pause')).toBeDisabled();
    expect(screen.getByTestId('call-control-dtmf-send')).toBeDisabled();
    expect(screen.getByTestId('call-control-transfer-cancel')).toBeDisabled();
  });

  it('gates transfer cancel and whisper behind an in-flight transfer', () => {
    renderWithClient(
      <CallControlBar
        callId="call-1"
        status="in_progress"
        transferState="none"
        capabilities={CAPABILITIES}
      />
    );
    expect(screen.getByTestId('call-control-transfer-cancel')).toBeDisabled();
    expect(screen.getByTestId('call-control-whisper-send')).toBeDisabled();
  });

  it('sends a DTMF batch and reports the per-item outcome', async () => {
    nextResponse = {
      ok: true,
      status: 200,
      body: {
        call_id: 'call-1',
        action: 'dtmf_batch',
        provider: 'twilio',
        items: [
          { index: 0, digits: '1', status: 'sent', idempotency_key: 'k0', replayed: false, error: null },
          { index: 1, digits: '2', status: 'sent', idempotency_key: 'k1', replayed: false, error: null },
        ],
        sent: 2,
        replayed: 0,
        rejected: 0,
        capabilities: CAPABILITIES,
        at: '2026-01-01T00:00:00+00:00',
      },
    };
    renderWithClient(
      <CallControlBar callId="call-1" status="in_progress" capabilities={CAPABILITIES} />
    );

    await userEvent.type(screen.getByTestId('call-control-dtmf-input'), '1 2');
    await userEvent.click(screen.getByTestId('call-control-dtmf-send'));

    await waitFor(() =>
      expect(screen.getByTestId('call-control-dtmf-result').textContent).toContain(
        'Sent 2 of 2'
      )
    );
    const body = recorded[0].body as {
      items: Array<{ digits: string; idempotency_key: string }>;
    };
    expect(body.items.map((i) => i.digits)).toEqual(['1', '2']);
  });

  it('shows the sanitized whisper text the server says it delivered', async () => {
    nextResponse = {
      ok: true,
      status: 200,
      body: {
        call_id: 'call-1',
        action: 'transfer_whisper',
        transfer_state: 'dialing',
        provider: 'twilio',
        provider_status: 'whisper_delivered',
        message: 'Customer is on a callback.',
        idempotency_key: 'w1',
        replayed: false,
        capabilities: CAPABILITIES,
        at: '2026-01-01T00:00:00+00:00',
      },
    };
    renderWithClient(
      <CallControlBar
        callId="call-1"
        status="in_progress"
        transferState="dialing"
        capabilities={CAPABILITIES}
      />
    );
    await userEvent.type(
      screen.getByTestId('call-control-whisper-input'),
      'Customer is on a callback.'
    );
    await userEvent.click(screen.getByTestId('call-control-whisper-send'));
    await waitFor(() =>
      expect(screen.getByTestId('call-control-whisper-result').textContent).toContain(
        'Customer is on a callback.'
      )
    );
  });

  it('renders the server error code inline when a control is refused', async () => {
    nextResponse = {
      ok: false,
      status: 409,
      body: { detail: { code: 'call_already_paused', message: 'already paused' } },
    };
    renderWithClient(
      <CallControlBar callId="call-1" status="in_progress" capabilities={CAPABILITIES} />
    );
    await userEvent.click(screen.getByTestId('call-control-pause'));
    await waitFor(() =>
      expect(screen.getByTestId('call-control-notice').textContent).toContain(
        'call_already_paused'
      )
    );
  });
});
