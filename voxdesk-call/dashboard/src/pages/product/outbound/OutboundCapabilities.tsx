import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const OUTBOUND_CAPS = [
  { title: 'Centralized DNC & Opt-Out Enforcement', desc: 'Every outbound dial checks tenant DNC registries and STOP/opt-out records before placing a call.' },
  { title: 'Recipient Timezone Calling Windows', desc: 'Enforce legal calling hours per recipient area code and state automatically.' },
  { title: 'Dynamic Variable Personalization', desc: 'Inject caller name, appointment details, and CRM fields into prompts via {{variable}} syntax.' },
  { title: 'Automated Retry & Disposition Logging', desc: 'Configure busy/no-answer retry backoff and write structured dispositions back to your CRM.' },
];

export function OutboundCapabilities() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <div className="grid gap-6 sm:grid-cols-2">
        {OUTBOUND_CAPS.map((c) => (
          <GlassCard key={c.title} className="p-6">
            <h2 className="text-base font-semibold text-white">{c.title}</h2>
            <p className="mt-2 text-xs leading-relaxed text-white/65">{c.desc}</p>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default OutboundCapabilities;
