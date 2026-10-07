import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

export function TelemarketingHero(props: Record<string, any>) {
  const itemTitle = props?.integration?.name || props?.industry?.name || 'AI Outbound & Lead Qualification';
  const itemDesc = props?.integration?.description || props?.industry?.description || 'Compliant outbound calling campaigns with dynamic variable personalization and instant warm transfer.';
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
export default TelemarketingHero;
