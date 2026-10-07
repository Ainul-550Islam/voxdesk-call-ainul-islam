import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function SLA() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Service commitments</p>
        <h1 className="mt-4 text-3xl font-bold text-white sm:text-4xl">Service-level agreement availability</h1>
        <div className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-6 text-sm leading-6 text-amber-50/80">
          This page does not publish an uptime percentage, support response guarantee, incident coverage promise, or automatic failover commitment. The numerical commitments previously shown here were not substantiated by an active contract or public status history and have been removed.
        </div>
        <p className="mt-6 text-sm leading-6 text-white/60">
          Service levels, support scope, and remedies are contract-specific. Confirm the applicable signed terms before relying on any availability or response-time commitment. The public status page reports only limited point-in-time component checks.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <a href="/status" className="inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">View limited public checks</a>
          <a href="/contact" className="inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Request service terms</a>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default SLA;
