import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function PricingEnterprise() {
  return (
    <section aria-labelledby="custom-terms-heading" className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <GlassCard className="p-8">
        <div className="grid gap-8 lg:grid-cols-12 lg:items-center">
          <div className="lg:col-span-8">
            <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
              Custom requirements
            </span>
            <h2 id="custom-terms-heading" className="mt-4 text-2xl font-bold text-white sm:text-3xl">
              Need a plan beyond the published catalogue?
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-white/60">
              Contact the team to review volume, provider configuration, security requirements, and commercial terms. A custom SLA, capacity reservation, or compliance agreement is not implied by this page and must be confirmed contractually.
            </p>
          </div>
          <div className="lg:col-span-4 lg:text-right">
            <a
              href="/contact-sales"
              className="inline-flex rounded-xl bg-white px-5 py-2.5 text-xs font-semibold text-black hover:bg-white/90"
            >
              Request a scoped quote
            </a>
          </div>
        </div>
      </GlassCard>
    </section>
  );
}

export default PricingEnterprise;
