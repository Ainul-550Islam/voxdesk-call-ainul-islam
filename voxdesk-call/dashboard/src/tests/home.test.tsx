import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { getAnalyticsSummary, getPublicHome } from '../api/home';
import type { AnalyticsSummaryResponse, HomeResponse } from '../types/home';
import { HomePage } from '../pages/home/HomePage';

vi.mock('../api/home', () => ({
  getPublicHome: vi.fn(),
  getAnalyticsSummary: vi.fn(),
}));

vi.mock('../api/voice-demo', () => ({
  createVoiceDemoSession: vi.fn().mockResolvedValue({
    status: 'ok',
    session_id: 'demo_123',
    expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    transport: 'webrtc',
    configuration: {
      session_id: 'demo_123',
      expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
      transport: 'webrtc',
      ice_servers: [{ urls: ['stun:stun.l.google.com:19302'] }],
      voice_states: ['IDLE', 'LISTENING', 'PROCESSING', 'SPEAKING', 'ERROR', 'NOT_CONFIGURED'],
      initial_state: 'IDLE',
      provider_configured: false,
      message: 'Demo not configured',
    },
  }),
  postVoiceDemoEvent: vi.fn().mockResolvedValue({
    status: 'ok',
    session_id: 'demo_123',
    event: 'start',
    new_state: 'LISTENING',
    at: new Date().toISOString(),
  }),
  getVoiceDemoSession: vi.fn().mockResolvedValue({
    status: 'ok',
    session_id: 'demo_123',
    state: 'IDLE',
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    events: [],
  }),
}));

const homeResponse: HomeResponse = {
  status: 'ok',
  data: {
    capabilities: [
      {
        id: 'build_agents',
        title: 'Build Voice Agents',
        description: 'Agent configuration routes are registered; provider readiness is not implied.',
        icon: 'bot',
        href: '/agent',
        category: 'core',
        status: 'PARTIAL',
        evidence_routes: ['/api/v1/agents'],
      },
    ],
    use_cases: [
      {
        id: 'support',
        title: 'Customer Support',
        description: 'A workflow category; tenant configuration is not implied.',
        icon: 'headset',
        href: '/solutions/support',
        status: 'PARTIAL',
        evidence_routes: ['/api/v1/calls'],
      },
    ],
    security_items: [
      {
        id: 'auth',
        title: 'Authentication',
        description: 'Authentication and role enforcement are served by the configured backend.',
        status: 'PARTIAL',
        evidence_routes: ['/api/v1/auth'],
      },
    ],
    developer_features: [
      {
        id: 'api',
        title: 'API-First',
        description: 'Registered API operations are listed as route evidence only.',
        docs_href: '/docs',
        status: 'PARTIAL',
        evidence_routes: ['/api/v1/agents'],
      },
    ],
  },
  meta: {
    generated_at: '2026-01-01T00:00:00Z',
    registered_api_operations: 1,
    evidence_scope: 'registered method/path pairs; not end-to-end verification',
  },
};

const analyticsResponse: AnalyticsSummaryResponse = {
  status: 'ok',
  data: {
    calls: null,
    successful_calls: null,
    average_duration_seconds: null,
    average_latency_ms: null,
    cost: null,
    status: 'empty',
    message: 'No call data yet',
  },
};

beforeEach(() => {
  vi.mocked(getPublicHome).mockResolvedValue(homeResponse);
  vi.mocked(getAnalyticsSummary).mockResolvedValue(analyticsResponse);
});

describe('HomePage', () => {
  it('renders header navigation', async () => {
    render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByText('VoxDesk')).toBeInTheDocument();
    });
  });

  it('renders CTA links', async () => {
    render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByText('Start Building')).toBeInTheDocument();
    });
  });

  it('renders the current hero copy', async () => {
    render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Build voice workflows for your business/i })).toBeInTheDocument();
    });
  });

  it('renders capabilities with route-evidence status', async () => {
    render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByText('Build Voice Agents')).toBeInTheDocument();
      expect(screen.getAllByText('PARTIAL · route evidence').length).toBeGreaterThan(0);
    });
  });

  it('renders the analytics empty state without fabricated metrics', async () => {
    render(<HomePage />);
    await waitFor(() => {
      const emptyStates = screen.getAllByText('No call data yet');
      expect(emptyStates.length).toBeGreaterThan(0);
      expect(emptyStates[0].closest('section')).not.toHaveTextContent(/Total Calls|Successful Calls/);
    });
  });
});
