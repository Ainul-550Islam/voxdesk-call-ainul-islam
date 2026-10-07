import React from 'react';
import { SectionHeader } from '../../components/ui/SectionHeader';

const STEPS = [
  { id: 'channel', label: 'Channel', detail: 'Configured phone, web, or API entry point', icon: '📱' },
  { id: 'agent', label: 'Agent runtime', detail: 'Selected agent version and runtime policy', icon: '🤖' },
  { id: 'providers', label: 'Model and voice providers', detail: 'Only providers configured for the workspace', icon: '🧠' },
  { id: 'tools', label: 'Knowledge and tools', detail: 'Workspace-authorized data and integrations', icon: '🔧' },
  { id: 'outcome', label: 'Call outcome', detail: 'Persisted call, transcript, and analysis where configured', icon: '📄' },
];

export function HomeArchitecture() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-black via-[#0a0a1a] to-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,#ffffff03_1px,transparent_1px),linear-gradient(to_bottom,#ffffff03_1px,transparent_1px)] bg-[size:4rem_4rem]" aria-hidden="true" />
      <div className="relative mx-auto max-w-7xl">
        <SectionHeader
          badge="Conceptual call flow"
          title="Trace the parts of a voice workflow"
          description="This is a product-level flow illustration, not a deployment topology, latency benchmark, concurrency guarantee, or statement that every external provider is configured."
          align="center"
        />
        <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {STEPS.map((step, index) => (
            <li key={step.id} className="relative rounded-2xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 text-lg" aria-hidden="true">{step.icon}</div>
              <div className="mt-4 text-[10px] font-semibold uppercase tracking-widest text-blue-300">Step {index + 1}</div>
              <h3 className="mt-2 text-sm font-semibold text-white">{step.label}</h3>
              <p className="mt-2 text-xs leading-relaxed text-white/55">{step.detail}</p>
            </li>
          ))}
        </ol>
        <p className="mt-6 text-center text-xs text-white/40">
          Availability and persisted outcomes must be verified in the workspace and with its configured provider.
        </p>
      </div>
    </section>
  );
}

export default HomeArchitecture;
