import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { UseCaseExampleCall } from '../pages/use-cases/UseCaseExampleCall';
import { UseCaseProcessTimeline } from '../components/use-cases/UseCaseProcessTimeline';
import type { UseCaseWorkflowStep } from '../types/use-case';

describe('Use Case Detail', () => {
  it('labels example conversation as Example not real', () => {
    const messages = [
      { role: 'user' as const, content: 'Hello' },
      { role: 'agent' as const, content: 'Hi there' },
    ];
    render(<UseCaseExampleCall messages={messages} />);
    expect(screen.getAllByText(/example conversation/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/demo only/i)).toBeInTheDocument();
  });

  it('renders workflow timeline with real data', () => {
    const steps: UseCaseWorkflowStep[] = [
      { order: 1, id: 'inbound', title: 'Inbound Call', description: 'Caller initiates call' },
      { order: 2, id: 'agent', title: 'Voice Agent', description: 'AI handles call' },
    ];
    render(<UseCaseProcessTimeline steps={steps} />);
    expect(screen.getByText('Inbound Call')).toBeInTheDocument();
    expect(screen.getByText('Voice Agent')).toBeInTheDocument();
  });

  it('shows not configured when workflow empty', () => {
    render(<UseCaseProcessTimeline steps={[]} />);
    expect(screen.getByText(/workflow details not configured/i)).toBeInTheDocument();
  });

  it('never shows fake customer labels', () => {
    const messages = [
      { role: 'user' as const, content: 'Test' },
      { role: 'agent' as const, content: 'Response' },
    ];
    render(<UseCaseExampleCall messages={messages} />);
    const text = document.body.textContent || '';
    expect(text).not.toContain('LIVE CALL');
    expect(text).not.toContain('CUSTOMER CALL');
    expect(text).not.toContain('REAL CALL');
    expect(screen.getAllByText(/example conversation/i).length).toBeGreaterThan(0);
  });

  it('renders workflow in order', () => {
    const steps: UseCaseWorkflowStep[] = [
      { order: 2, id: 'b', title: 'Second', description: 'Second step' },
      { order: 1, id: 'a', title: 'First', description: 'First step' },
    ];
    render(<UseCaseProcessTimeline steps={steps} />);
    const items = screen.getAllByRole('listitem');
    expect(items[0].textContent).toContain('First');
    expect(items[1].textContent).toContain('Second');
  });

  it('shows demo data disclaimer', () => {
    const messages = [{ role: 'user' as const, content: 'Hi' }];
    render(<UseCaseExampleCall messages={messages} />);
    expect(screen.getByText(/demo data only/i)).toBeInTheDocument();
  });

  it('handles empty messages', () => {
    render(<UseCaseExampleCall messages={[]} />);
    expect(screen.getByText(/no example conversation/i)).toBeInTheDocument();
  });
});
