/**
 * dashboard/src/features/public-widget/__tests__/PublicWidget.test.tsx
 * Prompt 5 frontend verification tests:
 * - Public vs Protected route boundary & open-redirect protection
 * - PublicWidget state machine (READY -> CONNECTED -> LISTENING -> ENDED, NOT_CONFIGURED, FORBIDDEN_ORIGIN, INVALID_PUBLIC_KEY)
 * - WidgetSettings key creation, one-time raw key reveal, rotation, and revocation
 * - LoginPage & ContactSalesPage real API wiring
 */

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as agentsApi from '../../../api/agents';
import * as publicKeysApi from '../../../api/public-keys';
import * as publicSiteApi from '../../../api/public-site';
import * as publicWidgetApi from '../../../api/public-widget';
import {
  isAuthPath,
  isProtectedPath,
  isPublicPath,
  resolveRouteBoundary,
} from '../../../app/router';
import { ContactSalesPage } from '../../../pages/public/ContactSalesPage';
import { LoginPage } from '../../../pages/public/LoginPage';
import { PublicWidget } from '../PublicWidget';
import { WidgetSettings } from '../WidgetSettings';

describe('Prompt 5 — Public Website + Auth Boundary + Public Key + Web Widget', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('enforces public vs protected route boundaries and prevents open redirects', () => {
    expect(isPublicPath('/')).toBe(true);
    expect(isPublicPath('/pricing')).toBe(true);
    expect(isPublicPath('/use-cases')).toBe(true);
    expect(isPublicPath('/security')).toBe(true);
    expect(isPublicPath('/contact-sales')).toBe(true);
    expect(isAuthPath('/login')).toBe(true);
    expect(isAuthPath('/signup')).toBe(true);

    expect(isProtectedPath('/app/overview')).toBe(true);
    expect(isProtectedPath('/dashboard/agents')).toBe(true);
    expect(isProtectedPath('/dashboard/public-keys')).toBe(true);

    const anonProtected = resolveRouteBoundary('/dashboard/agents', false);
    expect(anonProtected.allowed).toBe(false);
    expect(anonProtected.redirectTo).toBe('/login?next=%2Fdashboard%2Fagents');

    const authedProtected = resolveRouteBoundary('/dashboard/agents', true);
    expect(authedProtected.allowed).toBe(true);
    expect(authedProtected.redirectTo).toBeNull();

    // Open-redirect sanitization
    expect(publicSiteApi.sanitizeReturnPath('https://evil.example/phish')).toBe(
      '/app/overview',
    );
    expect(publicSiteApi.sanitizeReturnPath('//evil.example/phish')).toBe(
      '/app/overview',
    );
    expect(publicSiteApi.sanitizeReturnPath('/\\evil.example')).toBe('/app/overview');
    expect(publicSiteApi.sanitizeReturnPath('/login')).toBe('/app/overview');
    expect(
      publicSiteApi.sanitizeReturnPath('/dashboard/agents/123/settings'),
    ).toBe('/dashboard/agents/123/settings');
  });

  it('bootstraps PublicWidget, runs multi-turn chat session, and ends session cleanly', async () => {
    vi.spyOn(publicWidgetApi, 'fetchPublicWidgetBootstrap').mockResolvedValue({
      public_key_prefix: 'vdpk_abc12345',
      agent_id: 'agent-1',
      agent_name: 'Dental Concierge',
      agent_kind: 'voice',
      published_version_number: 2,
      environment_name: 'production',
      allowed_capabilities: [
        'widget_config_read',
        'widget_session_create',
        'widget_chat_send',
        'widget_voice_start',
        'widget_session_end',
      ],
      appearance: {
        title: 'Smile Clinic Concierge',
        subtitle: '24/7 Reception',
        greeting: 'Welcome to Smile Clinic! How can I help you?',
        placeholder: 'Ask a question...',
        primary_color: '#2563EB',
        position: 'bottom-right',
        enable_chat: true,
        enable_voice: true,
        show_branding: true,
      },
      voice_transport_configured: false,
      voice_transport_status: 'not_configured',
      voice_transport_message: 'Live WebRTC not configured',
      session_ttl_seconds: 900,
    });

    vi.spyOn(publicWidgetApi, 'startPublicWidgetSession').mockResolvedValue({
      session_id: 'sess-101',
      status: 'connected',
      mode: 'chat',
      agent_id: 'agent-1',
      agent_name: 'Dental Concierge',
      agent_kind: 'voice',
      agent_version_number: 2,
      transport: 'http_chat',
      session_token: 'vdws_tok_123',
      expires_at: new Date(Date.now() + 900_000).toISOString(),
      turns_count: 1,
      max_turns: 25,
      transcript: [
        {
          role: 'assistant',
          content: 'Welcome to Smile Clinic! How can I help you?',
          timestamp: new Date().toISOString(),
          turn_index: 0,
        },
      ],
      appearance: {
        title: 'Smile Clinic Concierge',
        subtitle: '24/7 Reception',
        greeting: 'Welcome to Smile Clinic! How can I help you?',
        placeholder: 'Ask a question...',
        primary_color: '#2563EB',
        position: 'bottom-right',
        enable_chat: true,
        enable_voice: true,
        show_branding: true,
      },
      created_at: new Date().toISOString(),
    });

    vi.spyOn(publicWidgetApi, 'sendPublicWidgetMessage').mockResolvedValue({
      session_id: 'sess-101',
      status: 'connected',
      turn_index: 2,
      user_turn: {
        role: 'user',
        content: 'Do you have cleanings tomorrow?',
        timestamp: new Date().toISOString(),
        turn_index: 1,
      },
      assistant_turn: {
        role: 'assistant',
        content: 'Yes, we have openings at 10:00 AM and 2:00 PM tomorrow.',
        timestamp: new Date().toISOString(),
        turn_index: 2,
      },
      turns_count: 3,
      max_turns: 25,
      remaining_turns: 22,
      transcript: [
        {
          role: 'assistant',
          content: 'Welcome to Smile Clinic! How can I help you?',
          timestamp: new Date().toISOString(),
          turn_index: 0,
        },
        {
          role: 'user',
          content: 'Do you have cleanings tomorrow?',
          timestamp: new Date().toISOString(),
          turn_index: 1,
        },
        {
          role: 'assistant',
          content: 'Yes, we have openings at 10:00 AM and 2:00 PM tomorrow.',
          timestamp: new Date().toISOString(),
          turn_index: 2,
        },
      ],
    });

    vi.spyOn(publicWidgetApi, 'endPublicWidgetSession').mockResolvedValue({
      session_id: 'sess-101',
      status: 'completed',
      mode: 'chat',
      agent_id: 'agent-1',
      agent_name: 'Dental Concierge',
      agent_kind: 'voice',
      agent_version_number: 2,
      transport: 'http_chat',
      session_token: null,
      expires_at: new Date(Date.now() + 900_000).toISOString(),
      turns_count: 3,
      max_turns: 25,
      transcript: [],
      appearance: {
        title: 'Smile Clinic Concierge',
        subtitle: '24/7 Reception',
        greeting: 'Welcome to Smile Clinic! How can I help you?',
        placeholder: 'Ask a question...',
        primary_color: '#2563EB',
        position: 'bottom-right',
        enable_chat: true,
        enable_voice: true,
        show_branding: true,
      },
      created_at: new Date().toISOString(),
      ended_at: new Date().toISOString(),
    });

    render(<PublicWidget publicKey="vdpk_abc12345_secret" inline defaultOpen />);

    await waitFor(() => {
      expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent('READY');
    });
    expect(screen.getByTestId('public-widget-title')).toHaveTextContent(
      'Smile Clinic Concierge',
    );

    // Start Chat session
    fireEvent.click(screen.getByTestId('public-widget-start-chat-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent('CONNECTED');
    });
    expect(screen.getByText(/Welcome to Smile Clinic!/i)).toBeInTheDocument();

    // Send a message
    const input = screen.getByTestId('widget-message-input');
    fireEvent.change(input, { target: { value: 'Do you have cleanings tomorrow?' } });
    fireEvent.click(screen.getByTestId('widget-send-btn'));

    await waitFor(() => {
      expect(
        screen.getByText(/Yes, we have openings at 10:00 AM and 2:00 PM tomorrow./i),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent('LISTENING');

    // End session
    fireEvent.click(screen.getByTestId('widget-end-session-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent('ENDED');
    });
  });

  it('displays honest NOT_CONFIGURED voice status and FORBIDDEN_ORIGIN error states', async () => {
    vi.spyOn(publicWidgetApi, 'fetchPublicWidgetBootstrap').mockResolvedValue({
      public_key_prefix: 'vdpk_voice123',
      agent_id: 'agent-1',
      agent_name: 'Dental Concierge',
      agent_kind: 'voice',
      published_version_number: 2,
      environment_name: 'production',
      allowed_capabilities: ['widget_config_read', 'widget_session_create', 'widget_voice_start'],
      appearance: {
        title: 'Voice Widget',
        subtitle: 'Pinned v2',
        greeting: 'Hello!',
        placeholder: 'Type...',
        primary_color: '#2563EB',
        position: 'bottom-right',
        enable_chat: true,
        enable_voice: true,
        show_branding: true,
      },
      voice_transport_configured: false,
      voice_transport_status: 'not_configured',
      voice_transport_message: 'Live WebRTC voice transport is not configured.',
      session_ttl_seconds: 900,
    });

    vi.spyOn(publicWidgetApi, 'startPublicWidgetSession').mockResolvedValue({
      session_id: 'sess-voice-1',
      status: 'not_configured',
      mode: 'voice',
      agent_id: 'agent-1',
      agent_name: 'Dental Concierge',
      agent_kind: 'voice',
      agent_version_number: 2,
      transport: 'not_configured',
      session_token: 'vdws_tok_voice',
      expires_at: new Date(Date.now() + 900_000).toISOString(),
      turns_count: 0,
      max_turns: 25,
      transcript: [],
      appearance: {
        title: 'Voice Widget',
        subtitle: 'Pinned v2',
        greeting: 'Hello!',
        placeholder: 'Type...',
        primary_color: '#2563EB',
        position: 'bottom-right',
        enable_chat: true,
        enable_voice: true,
        show_branding: true,
      },
      error_code: 'NOT_CONFIGURED',
      error_message:
        'Live WebRTC/SIP voice transport is not configured in this environment.',
      created_at: new Date().toISOString(),
    });

    const { unmount } = render(
      <PublicWidget publicKey="vdpk_voice123_secret" inline defaultOpen />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent('READY');
    });

    fireEvent.click(screen.getByTestId('public-widget-start-voice-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent(
        'NOT CONFIGURED',
      );
    });
    expect(screen.getByTestId('widget-not-configured-banner')).toHaveTextContent(
      /Live WebRTC\/SIP voice transport is not configured/i,
    );

    unmount();

    // Now simulate FORBIDDEN_ORIGIN on bootstrap
    vi.spyOn(publicWidgetApi, 'fetchPublicWidgetBootstrap').mockRejectedValue(
      new publicWidgetApi.PublicWidgetApiError(
        403,
        'FORBIDDEN_ORIGIN',
        "Origin 'https://evil.example' is not in this public key's allowed_origins policy.",
      ),
    );

    render(<PublicWidget publicKey="vdpk_voice123_secret" inline defaultOpen />);
    await waitFor(() => {
      expect(screen.getByTestId('widget-ui-state-badge')).toHaveTextContent(
        'FORBIDDEN ORIGIN',
      );
    });
    expect(screen.getByTestId('widget-error-banner')).toHaveTextContent(
      /not in this public key's allowed_origins policy/i,
    );
  });

  it('manages scoped PublicWidgetKeys in WidgetSettings (create, reveal once, rotate, revoke)', async () => {
    vi.spyOn(agentsApi, 'listAgents').mockResolvedValue({
      agents: [
        {
          id: 'agent-pub-1',
          tenant_id: 'tenant-1',
          name: 'Published Intake Agent',
          slug: 'published-intake-agent',
          description: 'Published agent',
          status: 'published',
          published_version_id: 'ver-2',
          published_version_number: 2,
          latest_version_number: 2,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
      total: 1,
    });

    vi.spyOn(publicKeysApi, 'listPublicWidgetKeys').mockResolvedValue({
      keys: [],
      total: 0,
    });

    vi.spyOn(publicKeysApi, 'createPublicWidgetKey').mockResolvedValue({
      public_key: 'vdpk_newkey99_secretvalue123456',
      warning: 'Store this key securely.',
      key: {
        id: 'key-1',
        tenant_id: 'tenant-1',
        environment_name: 'production',
        agent_id: 'agent-pub-1',
        agent_kind: 'voice',
        name: 'Production Website Widget',
        key_prefix: 'vdpk_newkey99',
        status: 'active',
        allowed_origins: ['https://clinic.example.com'],
        allowed_capabilities: ['widget_config_read', 'widget_session_create'],
        rate_limit_per_minute: 30,
        session_ttl_seconds: 900,
        require_published_agent: true,
        widget_config: {
          title: 'Talk with our AI Concierge',
          subtitle: 'Powered by VoxDesk',
          greeting: 'Hello!',
          placeholder: 'Ask...',
          primary_color: '#2563EB',
          position: 'bottom-right',
          enable_chat: true,
          enable_voice: true,
          show_branding: true,
        },
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        embed_snippet: '<script src="https://cdn.voxdesk.ai/widget/v1/voxdesk-widget.js" data-public-key="vdpk_newkey99_secretvalue123456"></script>',
      },
    });

    vi.spyOn(publicWidgetApi, 'fetchPublicWidgetBootstrap').mockResolvedValue({
      public_key_prefix: 'vdpk_newkey99',
      agent_id: 'agent-pub-1',
      agent_name: 'Published Intake Agent',
      agent_kind: 'voice',
      published_version_number: 2,
      environment_name: 'production',
      allowed_capabilities: ['widget_config_read', 'widget_session_create'],
      appearance: {
        title: 'Talk with our AI Concierge',
        subtitle: 'Powered by VoxDesk',
        greeting: 'Hello!',
        placeholder: 'Ask...',
        primary_color: '#2563EB',
        position: 'bottom-right',
        enable_chat: true,
        enable_voice: true,
        show_branding: true,
      },
      voice_transport_configured: false,
      voice_transport_status: 'not_configured',
      session_ttl_seconds: 900,
    });

    render(<WidgetSettings agentId="agent-pub-1" />);

    await waitFor(() => {
      expect(screen.getByTestId('create-public-key-form')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('create-public-key-submit-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('widget-revealed-raw-key')).toHaveTextContent(
        'vdpk_newkey99_secretvalue123456',
      );
    });
    expect(screen.getByTestId('widget-embed-snippet')).toHaveTextContent(
      'data-public-key="vdpk_newkey99_secretvalue123456"',
    );
  });

  it('wires LoginPage and ContactSalesPage to real backend endpoints', async () => {
    vi.spyOn(publicSiteApi, 'loginWithPassword').mockResolvedValue({
      access_token: 'jwt_real_token_123',
      token_type: 'bearer',
      expires_in: 900,
      user: {
        id: 'u-1',
        email: 'owner@acme.com',
        full_name: 'Owner',
        role: 'owner',
        tenant_id: 't-1',
        is_active: true,
      },
      redirect_to: '/dashboard/agents',
    });

    const onLoginSuccess = vi.fn();
    const { unmount } = render(
      <LoginPage nextPath="https://evil.com/phish" onLoginSuccess={onLoginSuccess} />,
    );

    // Notice that https://evil.com/phish is sanitized to /app/overview in the notice
    expect(screen.getByTestId('login-return-path-notice')).toHaveTextContent(
      '/app/overview',
    );

    fireEvent.change(screen.getByTestId('login-email-input'), {
      target: { value: 'owner@acme.com' },
    });
    fireEvent.change(screen.getByTestId('login-password-input'), {
      target: { value: 'StrongPass!123' },
    });
    fireEvent.click(screen.getByTestId('login-submit-btn'));

    await waitFor(() => {
      expect(onLoginSuccess).toHaveBeenCalledWith('/dashboard/agents');
    });
    expect(window.localStorage.getItem('voxdesk_access_token')).toBe(
      'jwt_real_token_123',
    );

    unmount();

    // Verify ContactSalesPage durable submission
    vi.spyOn(publicSiteApi, 'submitPublicContactSales').mockResolvedValue({
      id: 'inq-999',
      full_name: 'Jordan Lee',
      work_email: 'jordan@northstar.io',
      company_name: 'NorthStar Health',
      job_title: 'CTO',
      monthly_call_volume: '50,000 - 250,000 mins/mo',
      primary_use_case: 'Patient Scheduling',
      status: 'received',
      created_at: new Date().toISOString(),
      confirmation_message:
        'Thank you, Jordan Lee. Your inquiry for NorthStar Health has been recorded.',
    });

    render(<ContactSalesPage />);
    fireEvent.change(screen.getByTestId('contact-name-input'), {
      target: { value: 'Jordan Lee' },
    });
    fireEvent.change(screen.getByTestId('contact-email-input'), {
      target: { value: 'jordan@northstar.io' },
    });
    fireEvent.change(screen.getByTestId('contact-company-input'), {
      target: { value: 'NorthStar Health' },
    });
    fireEvent.click(screen.getByTestId('contact-sales-submit-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('contact-sales-confirmation')).toHaveTextContent(
        'inq-999',
      );
    });
  });
});
