/**
 * The playground must never silently allow a mock provider to answer.
 *
 * Before this change the run payload hard-coded `allow_mock_fallback: true`,
 * so every playground and simulation run could be answered by a mock and the
 * result would still be filed alongside real evidence. These tests pin the new
 * behaviour: the flag follows an explicit, visible toggle that defaults OFF.
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AgentPlayground } from '../pages/product/AgentPlayground';

const postedBodies: Array<Record<string, unknown>> = [];

beforeEach(() => {
  postedBodies.length = 0;
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
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL, init?: RequestInit) => {
      if (init?.body && (init.method ?? 'GET').toUpperCase() === 'POST') {
        postedBodies.push(JSON.parse(String(init.body)));
      }
      return new Response(
        JSON.stringify({
          id: 'run-1',
          status: 'passed',
          mode: 'llm',
          provider: 'openai',
          agent_version_number: 1,
          is_mock_provider: false,
          transcript_snapshot: [],
          events_snapshot: [],
          evaluation_results: [],
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      );
    }) as unknown as typeof fetch
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function runPlayground() {
  // The submit button inside the playground form; it is the only control that
  // actually posts a run.
  await userEvent.click(screen.getByTestId('execute-playground-btn'));
}

describe('AgentPlayground sandbox mode', () => {
  it('defaults to OFF and submits allow_mock_fallback: false', async () => {
    render(<AgentPlayground />);
    const toggle = screen.getByTestId('sandbox-mode-toggle');
    expect(toggle).not.toBeChecked();
    expect(screen.queryByTestId('sandbox-mode-badge')).toBeNull();

    await runPlayground();
    expect(postedBodies.length).toBeGreaterThan(0);
    expect(postedBodies[0].allow_mock_fallback).toBe(false);
  });

  it('shows a persistent sandbox badge and sends true only when enabled', async () => {
    render(<AgentPlayground />);
    await userEvent.click(screen.getByTestId('sandbox-mode-toggle'));

    const badge = screen.getByTestId('sandbox-mode-badge');
    expect(badge.textContent).toBe('SANDBOX — not a real call');
    // Persistent, not a transient toast: it survives a run.
    await runPlayground();
    expect(screen.getByTestId('sandbox-mode-badge')).toBeTruthy();
    expect(postedBodies[0].allow_mock_fallback).toBe(true);
  });

  it('removes the badge when the toggle is switched back off', async () => {
    render(<AgentPlayground />);
    const toggle = screen.getByTestId('sandbox-mode-toggle');
    await userEvent.click(toggle);
    expect(screen.getByTestId('sandbox-mode-badge')).toBeTruthy();
    await userEvent.click(toggle);
    expect(screen.queryByTestId('sandbox-mode-badge')).toBeNull();
  });
});
