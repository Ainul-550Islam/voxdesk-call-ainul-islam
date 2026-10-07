import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const PLAYBOOKS = [
  { tag: 'Architecture', title: 'Designing Low-Latency Duplex Voice Pipelines with Rust & C++ SIMD', href: '/docs' },
  { tag: 'Telephony', title: 'SIP Trunking, E.164 Provisioning & Warm Transfer State Machines', href: '/docs' },
  { tag: 'RAG & Memory', title: 'Cross-Call Contact Memory & Guardrailed Prompt Variable Interpolation', href: '/docs' },
  { tag: 'Compliance', title: 'Implementing Automated DNC, Calling Windows & Two-Party Recording Consent', href: '/compliance' },
];

export function ResourceGuides() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 className="text-2xl font-bold text-white">Technical Playbooks</h2>
      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        {PLAYBOOKS.map((p) => (
          <GlassCard key={p.title} className="p-6">
            <span className="rounded-full bg-blue-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-blue-300">{p.tag}</span>
            <h3 className="mt-3 text-base font-semibold text-white">{p.title}</h3>
            <a href={p.href} className="mt-4 inline-flex text-xs font-semibold text-blue-400 hover:text-blue-300">Read Guide →</a>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default ResourceGuides;
