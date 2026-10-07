import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const INBOUND_CAPS = [
  { title: 'Caller Recognition & Contact Memory', desc: 'Recognize returning callers by E.164 number and recall prior preferences and call summaries.' },
  { title: 'Multi-Level IVR & Intent Routing', desc: 'Route callers by spoken intent or DTMF input to specialized departmental agents.' },
  { title: 'Real-Time Calendar & CRM Tools', desc: 'Book appointments, check order status, and create support tickets mid-call.' },
  { title: 'Warm Human Handoff with Whisper', desc: 'Brief live agents with a concise summary before bridging the caller.' },
];

export function InboundCapabilities() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <div className="grid gap-6 sm:grid-cols-2">
        {INBOUND_CAPS.map((c) => (
          <GlassCard key={c.title} className="p-6">
            <h2 className="text-base font-semibold text-white">{c.title}</h2>
            <p className="mt-2 text-xs leading-relaxed text-white/65">{c.desc}</p>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default InboundCapabilities;
