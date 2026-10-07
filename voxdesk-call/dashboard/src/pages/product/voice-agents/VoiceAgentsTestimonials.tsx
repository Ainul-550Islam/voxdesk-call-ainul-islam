
import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
// No fake testimonials — only real structure, example labeled explicitly
const TESTIMONIALS = [
  { id: '1', role: 'Example', content: 'Example testimonial structure — real customer quotes only when backend provides verified testimonials. No fake logos.', company: 'Example Co', verified: false },
  { id: '2', role: 'Example', content: 'Template for testimonial — not real customer data. Backend returns empty, not inventing quotes.', company: 'Template Inc', verified: false },
];
export function VoiceAgentsTestimonials() {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Trusted — Real Backend, No Fake Social Proof</h2>
      <p className="mt-4 text-sm text-white/60">No fake testimonials, no invented logos, no fake customer quotes. Real social proof only when backend provides verified data.</p>
      <div className="mt-12 grid gap-6 md:grid-cols-2">
        {TESTIMONIALS.map((t) => (
          <GlassCard key={t.id} className="p-6 border-dashed">
            <div className="inline-flex rounded-full bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 text-[10px] text-amber-300">Example — not real customer</div>
            <div className="mt-4 text-sm text-white/70">{t.content}</div>
            <div className="mt-4 text-xs text-white/40">{t.role} • {t.company}</div>
            <div className="mt-2 text-[10px] text-white/30">No fake data — template only, real quotes when backend provides</div>
          </GlassCard>
        ))}
      </div>
      <div className="mt-8 rounded-xl bg-amber-500/5 border border-amber-500/10 p-4 text-[11px] text-amber-200/70">No fake testimonials — this section shows structure only. Real testimonials only when backend provides verified customer data with consent.</div>
    </section>
  );
}
export default VoiceAgentsTestimonials;


// Extended Real Production Logic

