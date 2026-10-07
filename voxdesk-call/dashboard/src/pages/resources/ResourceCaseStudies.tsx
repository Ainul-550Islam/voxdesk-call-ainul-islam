import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const BLUEPRINTS = [
  { industry: 'Healthcare', title: 'Multi-Location Dental & Clinic Scheduling Blueprint', summary: 'Automates inbound appointment booking, insurance verification prompts, and SMS reminders.' },
  { industry: 'Home Services', title: '24/7 Emergency Dispatch & Technician Escalation Blueprint', summary: 'Triages urgent calls after hours and warm-transfers emergency dispatches with caller context.' },
  { industry: 'B2B Revenue Ops', title: 'Inbound Lead Qualification & Instant Calendar Handoff', summary: 'Qualifies high-intent leads, syncs structured fields to HubSpot/Salesforce, and books AE meetings.' },
];

export function ResourceCaseStudies() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 className="text-2xl font-bold text-white">Reference Implementation Blueprints</h2>
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        {BLUEPRINTS.map((b) => (
          <GlassCard key={b.title} className="p-6">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-violet-300">{b.industry}</span>
            <h3 className="mt-2 text-base font-semibold text-white">{b.title}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/60">{b.summary}</p>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default ResourceCaseStudies;
