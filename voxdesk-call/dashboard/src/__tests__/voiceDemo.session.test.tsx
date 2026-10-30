import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { client } from '../api/client';
import { VoiceDemoPage } from '../pages/public/VoiceDemoPage';
import { isPublicPath, matchRoute } from '../app/router';

describe('Public Voice Demo Session UI & HMAC token propagation', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('registers /voice-demo as a public route', () => {
    expect(matchRoute('/voice-demo')?.route.component).toBe('VoiceDemoPage');
    expect(isPublicPath('/voice-demo')).toBe(true);
  });

  it('creates a durable session, propagates X-Demo-Session-Token on start, turn, and end', async () => {
    const postSpy = vi.spyOn(client, 'post').mockImplementation(async (path: string, body?: any) => {
      if (path === '/api/v1/public/voice-demo/session') {
        return {
          status: 'ok',
          session_id: 'sess-demo-999',
          session_token: 'vds1.payload.hmacsig',
          agent_id: 'agt-demo-1',
          agent_name: 'Concierge Demo',
          expires_at: new Date(Date.now() + 300_000).toISOString(),
          transport: 'ws-protobuf',
          transcript: [
            { role: 'assistant', text: 'Welcome to Concierge Demo!', timestamp: '2026-10-29T00:00:00Z' },
          ],
          configuration: {
            session_id: 'sess-demo-999',
            expires_at: new Date(Date.now() + 300_000).toISOString(),
            transport: 'ws-protobuf',
            ice_servers: [],
            voice_states: ['IDLE', 'LISTENING', 'SPEAKING', 'ERROR'],
            initial_state: 'IDLE',
            provider_configured: true,
            message: 'Connected',
          },
        } as any;
      }
      if (path.endsWith('/event')) {
        return {
          status: 'ok',
          session_id: 'sess-demo-999',
          event: 'start',
          new_state: 'LISTENING',
          at: '2026-10-29T00:00:01Z',
          transcript: [
            { role: 'assistant', text: 'Welcome to Concierge Demo!', timestamp: '2026-10-29T00:00:00Z' },
          ],
        } as any;
      }
      if (path.endsWith('/turn')) {
        return {
          status: 'ok',
          session_id: 'sess-demo-999',
          event: 'turn',
          new_state: 'SPEAKING',
          at: '2026-10-29T00:00:02Z',
          reply: 'We are open 9am to 6pm.',
          transcript: [
            { role: 'assistant', text: 'Welcome to Concierge Demo!', timestamp: '2026-10-29T00:00:00Z' },
            { role: 'user', text: body.text, timestamp: '2026-10-29T00:00:02Z' },
            { role: 'assistant', text: 'We are open 9am to 6pm.', timestamp: '2026-10-29T00:00:02Z' },
          ],
        } as any;
      }
      return {
        status: 'ok',
        session_id: 'sess-demo-999',
        event: 'end',
        new_state: 'IDLE',
        at: '2026-10-29T00:00:03Z',
      } as any;
    });

    render(<VoiceDemoPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Start Demo Session' }));

    await waitFor(() => {
      expect(screen.getByTestId('voice-demo-state').textContent).toBe('LISTENING');
    });
    expect(screen.getByText(/Welcome to Concierge Demo!/)).toBeTruthy();

    // Verify X-Demo-Session-Token was forwarded on /event
    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/voice-demo/session/sess-demo-999/event',
      expect.objectContaining({ event: 'start' }),
      expect.objectContaining({
        authenticated: false,
        headers: { 'X-Demo-Session-Token': 'vds1.payload.hmacsig' },
      }),
    );

    const input = screen.getByLabelText('Say something to the demo agent');
    fireEvent.change(input, { target: { value: 'What are your hours?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send Turn' }));

    await waitFor(() => {
      expect(screen.getByText(/We are open 9am to 6pm\./)).toBeTruthy();
    });
    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/voice-demo/session/sess-demo-999/turn',
      { text: 'What are your hours?', session_token: 'vds1.payload.hmacsig' },
      expect.objectContaining({
        authenticated: false,
        headers: { 'X-Demo-Session-Token': 'vds1.payload.hmacsig' },
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'End Session' }));
    await waitFor(() => {
      expect(screen.getByTestId('voice-demo-state').textContent).toBe('IDLE');
    });
  });
});
