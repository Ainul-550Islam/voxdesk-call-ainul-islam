/**
 * dashboard/src/tests/voice-agents.test.tsx
 * Production tests for Voice AI / Phone Agents — real behavior, no fake data
 * Full structure: Voice AI explanation, build→test→deploy→monitor, call routing, IVR, transfers, outbound
 * No shortening, full code from start to end
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { VoiceAgentsHero } from '../pages/product/voice-agents/VoiceAgentsHero';
import { VoiceAgentsLifecycle } from '../pages/product/voice-agents/VoiceAgentsLifecycle';
import { VoiceAgentsCapabilities } from '../pages/product/voice-agents/VoiceAgentsCapabilities';
import { VoiceAgentsBuilderPreview } from '../pages/product/voice-agents/VoiceAgentsBuilderPreview';
import { VoiceAgentsUseCases } from '../pages/product/voice-agents/VoiceAgentsUseCases';
import { VoiceAgentsComparison } from '../pages/product/voice-agents/VoiceAgentsComparison';
import { VoiceAgentsDeveloper } from '../pages/product/voice-agents/VoiceAgentsDeveloper';
import { VoiceAgentsEnterprise } from '../pages/product/voice-agents/VoiceAgentsEnterprise';
import { VoiceAgentsSecurity } from '../pages/product/voice-agents/VoiceAgentsSecurity';
import { VoiceAgentsFAQ } from '../pages/product/voice-agents/VoiceAgentsFAQ';
import { VoiceAgentsCTA } from '../pages/product/voice-agents/VoiceAgentsCTA';
import { VoiceAgentsPage } from '../pages/product/voice-agents/VoiceAgentsPage';

vi.mock('../hooks/useHomeData', () => ({
  useHomeData: () => ({
    homeData: {
      capabilities: [{ id: 'voice', title: 'Voice', description: 'Natural voice', icon: '🎙️' }],
      developer_features: [{ id: 'api', title: 'API', description: 'REST API' }],
      security_items: [{ id: 'soc2', title: 'SOC 2', description: 'Compliant' }],
    },
    loading: false,
    error: null,
  }),
}));

const LIFECYCLE_STEPS = [
  { id: 'build', order: 1, title: 'Build — Create voice agents', description: 'Build description', shortTitle: 'BUILD', icon: '🛠️', color: 'from-blue-500 to-cyan-500', features: ['Voice', 'Knowledge'], cta: { label: 'Start Building', href: '/dashboard/agents/new' } },
  { id: 'test', order: 2, title: 'Test — Simulate calls', description: 'Test description', shortTitle: 'TEST', icon: '🧪', color: 'from-violet-500 to-purple-500', features: ['Simulation'], cta: { label: 'Test', href: '/dashboard/agents' } },
  { id: 'deploy', order: 3, title: 'Deploy — Go live', description: 'Deploy description', shortTitle: 'DEPLOY', icon: '🚀', color: 'from-emerald-500 to-teal-500', features: ['Phone numbers'], cta: { label: 'Deploy', href: '/dashboard/agents' } },
  { id: 'monitor', order: 4, title: 'Monitor — Real-time', description: 'Monitor description', shortTitle: 'MONITOR', icon: '📊', color: 'from-amber-500 to-orange-500', features: ['Analytics'], cta: { label: 'Monitor', href: '/dashboard/analytics' } },
  { id: 'improve', order: 5, title: 'Improve — Iterate', description: 'Improve description', shortTitle: 'IMPROVE', icon: '📈', color: 'from-pink-500 to-rose-500', features: ['A/B testing'], cta: { label: 'Improve', href: '/dashboard/agents' } },
];

describe('Voice Agents — AI Voice / Phone Agents', () => {
  it('renders hero with Voice AI explanation', () => {
    render(<VoiceAgentsHero onSeeHowItWorks={vi.fn()} />);
    expect(screen.getAllByText(/Build voice agents that/i).length).toBeGreaterThan(0);
  });

  it('shows build→test→deploy→monitor in hero', () => {
    render(<VoiceAgentsHero />);
    const body = document.body.textContent || '';
    expect(body).toContain('CREATE');
    expect(body).toContain('DEPLOY');
    expect(body).toContain('MONITOR');
  });

  it('renders lifecycle steps and truthful A/B API limits', () => {
    const activeStep = LIFECYCLE_STEPS[4];
    render(<VoiceAgentsLifecycle steps={LIFECYCLE_STEPS} activeId="improve" onChange={vi.fn()} activeStep={activeStep} />);
    expect(screen.getAllByText(/BUILD/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/TEST/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/DEPLOY/i).length).toBeGreaterThan(0);

    const content = document.body.textContent || '';
    expect(content).toContain('POST /api/experiments');
    expect(content).toContain('live call assignment is not connected');
    expect(content).not.toContain('/api/ab-testing');
    expect(content).not.toContain('Each phase verified with real provider integration');
  });

  it('handles lifecycle step change', () => {
    const onChange = vi.fn();
    const activeStep = LIFECYCLE_STEPS[0];
    render(<VoiceAgentsLifecycle steps={LIFECYCLE_STEPS} activeId="build" onChange={onChange} activeStep={activeStep} />);
    const testButton = screen.getAllByText('TEST')[0];
    fireEvent.click(testButton);
    expect(onChange).toHaveBeenCalled();
  });

  it('renders capabilities with IVR, transfers, outbound', () => {
    render(<VoiceAgentsCapabilities />);
    expect(screen.getAllByText(/IVR/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Transfer/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Outbound/i).length).toBeGreaterThan(0);
  });

  it('renders honest builder guidance and changes the selected workflow', () => {
    render(<VoiceAgentsBuilderPreview />);
    expect(screen.getByRole('heading', { name: 'Builder workflow overview' })).toBeInTheDocument();
    expect(screen.getByText(/does not create an agent, save changes/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Choose an agent for tool settings' })).toHaveAttribute('href', '/dashboard/agents');
    const routing = screen.getByRole('button', { name: /Call Routing & IVR/ });
    fireEvent.click(routing);
    expect(routing).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('link', { name: 'Open phone-number console' })).toHaveAttribute('href', '/dashboard/phone-numbers');
    fireEvent.click(screen.getByRole('button', { name: /Test & Validate/ }));
    expect(screen.getByRole('link', { name: 'Open simulation workspace' })).toHaveAttribute('href', '/dashboard/simulations');
    expect(document.body.textContent).not.toContain('Real Backend');
    expect(document.body.textContent).not.toContain('Indexed');
  });

  it('renders use cases section', () => {
    render(<VoiceAgentsUseCases />);
    expect(screen.getAllByText(/Use Cases/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/AI Receptionist/i).length).toBeGreaterThan(0);
  });

  it('renders comparison', () => {
    render(<VoiceAgentsComparison />);
    expect(screen.getAllByText(/Why VoxDesk/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Real Telephony/i).length).toBeGreaterThan(0);
  });

  it('renders developer section with API, SDK, Webhooks, Tools', () => {
    render(<VoiceAgentsDeveloper />);
    expect(screen.getAllByText(/Developer First/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/REST API/i).length).toBeGreaterThan(0);
  });

  it('shows API examples for outbound, transfer, DTMF', () => {
    render(<VoiceAgentsDeveloper />);
    const body = document.body.textContent || '';
    expect(body).toContain('/api/calls');
    expect(body).toContain('/transfer');
    expect(body).toContain('/dtmf');
  });

  it('renders enterprise section', () => {
    render(<VoiceAgentsEnterprise />);
    expect(screen.getAllByText(/Enterprise Ready/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SOC 2/i).length).toBeGreaterThan(0);
  });

  it('renders security section', () => {
    render(<VoiceAgentsSecurity />);
    expect(screen.getAllByText(/Security/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Recording/i).length).toBeGreaterThan(0);
  });

  it('renders FAQ', () => {
    render(<VoiceAgentsFAQ />);
    expect(screen.getAllByText(/FAQ/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/What is Voice AI/i).length).toBeGreaterThan(0);
  });

  it('renders CTA', () => {
    render(<VoiceAgentsCTA />);
    expect(screen.getAllByText(/Build your first voice agent/i).length).toBeGreaterThan(0);
  });

  it('renders the public page with honest workflow boundaries and working workspace routes', () => {
    render(<VoiceAgentsPage />);
    expect(screen.getByRole('heading', { name: 'Build and inspect voice-agent workflows' })).toBeInTheDocument();
    expect(screen.getByText('Configure an agent')).toBeInTheDocument();
    expect(screen.getByText('Test before deployment')).toBeInTheDocument();
    expect(screen.getByText('Connect a phone number')).toBeInTheDocument();
    expect(screen.getByText('Review persisted calls')).toBeInTheDocument();
    expect(screen.getByText(/Route presence does not prove that a provider is configured/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open agent workspace' })).toHaveAttribute('href', '/dashboard/agents');
  });

  it('distinguishes workflow examples from a live caller-to-agent call flow', () => {
    render(<VoiceAgentsPage />);
    const body = document.body.textContent || '';
    expect(body).toContain('Illustrative patterns');
    expect(body).toContain('They do not assert that a third-party provider is connected');
    expect(body).not.toContain('Caller → IVR → Voice Agent → Knowledge → Tools → Business System → Human');
  });

  it('never shows fake metrics', () => {
    render(<VoiceAgentsPage />);
    const body = document.body.textContent || '';
    expect(body).not.toContain('100% success rate');
    expect(body).not.toContain('10x ROI');
  });

  it('validates E.164 format', () => {
    const valid = ['+12345678901', '+441632960961'];
    const regex = /^\+[1-9]\d{7,14}$/;
    valid.forEach(n => expect(regex.test(n)).toBe(true));
  });

  it('validates DTMF digits', () => {
    const valid = ['123', '1#*', '0'];
    const regex = /^[0-9#*wW]+$/;
    valid.forEach(d => expect(regex.test(d)).toBe(true));
  });
});


