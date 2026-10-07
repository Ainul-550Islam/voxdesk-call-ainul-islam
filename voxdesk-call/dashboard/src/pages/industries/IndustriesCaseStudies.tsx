import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function IndustriesCaseStudies(props: Record<string, any>) {
  const itemTitle = props?.integration?.name || props?.industry?.name || 'Industry Deployment Blueprints';
  const itemDesc = props?.integration?.description || props?.industry?.description || 'Verified voice workflow architectures across healthcare, home services, legal, and finance.';
  return (
    <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <GlassCard className="p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">{itemTitle}</h2>
          <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[10px] font-medium text-emerald-300">
            Verified
          </span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-white/65">{itemDesc}</p>
      </GlassCard>
    </section>
  );
}
export default IndustriesCaseStudies;
