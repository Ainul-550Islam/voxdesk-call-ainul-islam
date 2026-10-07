/** Public workflow navigation, not a saved agent or a live provider session. */
import React, { useState } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

interface BuilderStep {
  id: string;
  title: string;
  description: string;
  detail: string;
  href: string;
  linkLabel: string;
}

const BUILDER_STEPS: BuilderStep[] = [
  {
    id: 'voice', title: 'Voice & Language',
    description: 'Review voice and language configuration',
    detail: 'Select an existing agent in the authenticated workspace. Available voices depend on the provider configuration; this overview does not verify a voice or save a selection.',
    href: '/dashboard/agents', linkLabel: 'Open agent workspace',
  },
  {
    id: 'knowledge', title: 'Knowledge Base',
    description: 'Review documents and retrieval settings',
    detail: 'Inspect uploaded sources and their persisted indexing status in the workspace. No sample document shown here is represented as an indexed source.',
    href: '/dashboard/agents', linkLabel: 'Choose an agent for knowledge settings',
  },
  {
    id: 'tools', title: 'Tools & Integrations',
    description: 'Review tool schemas and provider connections',
    detail: 'Configure tools on an existing agent and verify each connection separately. A tool schema does not prove that a third-party action has succeeded.',
    href: '/dashboard/agents', linkLabel: 'Choose an agent for tool settings',
  },
  {
    id: 'routing', title: 'Call Routing & IVR',
    description: 'Review number and routing configuration',
    detail: 'Inspect the tenant-owned phone number, environment, and published agent version. Live inbound calls require a configured carrier and runtime.',
    href: '/dashboard/phone-numbers', linkLabel: 'Open phone-number console',
  },
  {
    id: 'test', title: 'Test & Validate',
    description: 'Inspect persisted simulation runs',
    detail: 'Run and inspect tests in the authenticated workspace. A deterministic simulation is not evidence of a successful live phone call.',
    href: '/dashboard/simulations', linkLabel: 'Open simulation workspace',
  },
];

export function VoiceAgentsBuilderPreview() {
  const [activeId, setActiveId] = useState('tools');
  const activeStep = BUILDER_STEPS.find((step) => step.id === activeId)!;

  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <div className="max-w-3xl">
        <h2 className="text-3xl font-bold text-white sm:text-4xl">Builder workflow overview</h2>
        <p className="mt-4 text-[15px] text-white/60">
          Explore configuration steps. This public overview does not create an agent, save changes, index documents, or initiate a call.
        </p>
      </div>
      <div className="mt-12 grid gap-8 lg:grid-cols-3">
        <div className="space-y-3" aria-label="Builder workflow steps">
          {BUILDER_STEPS.map((step, index) => (
            <button
              key={step.id}
              type="button"
              aria-pressed={activeId === step.id}
              aria-controls="builder-workflow-detail"
              onClick={() => setActiveId(step.id)}
              className={`w-full text-left rounded-[14px] border p-4 transition-colors ${activeId === step.id ? 'bg-white text-black border-white' : 'bg-white/[0.03] border-white/10 text-white/60 hover:bg-white/[0.05]'}`}
            >
              <div className="text-sm font-medium">{index + 1}. {step.title}</div>
              <div className="mt-1 text-[11px] opacity-70">{step.description}</div>
            </button>
          ))}
        </div>
        <div className="lg:col-span-2">
          <GlassCard className="p-6">
            <div id="builder-workflow-detail" aria-live="polite">
              <p className="text-xs text-white/50">Workflow guidance — no agent selected</p>
              <h3 className="mt-3 text-xl font-semibold text-white">{activeStep.title}</h3>
              <p className="mt-4 text-sm leading-relaxed text-white/60">{activeStep.detail}</p>
              <a className="mt-6 inline-block rounded-xl bg-white px-4 py-2 text-xs text-black" href={activeStep.href}>
                {activeStep.linkLabel}
              </a>
            </div>
          </GlassCard>
        </div>
      </div>
    </section>
  );
}

export default VoiceAgentsBuilderPreview;
