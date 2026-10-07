import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function CareersPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">Careers</p>
        <h1 className="mt-4 text-4xl font-bold tracking-tight text-white sm:text-5xl">Open roles</h1>
        <p className="mt-5 text-base leading-7 text-white/65">
          No verified job openings are published on this site right now. The roles previously listed here were not connected to a live hiring process and have been removed rather than presented as active vacancies.
        </p>
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <h2 className="text-base font-semibold text-white">Ask about future opportunities</h2>
          <p className="mt-3 text-sm leading-6 text-white/60">Use the contact form to send an inquiry. A form submission records an inquiry; it is not an application or a promise of a response time.</p>
          <a href="/contact" className="mt-5 inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Contact VoxDesk</a>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default CareersPage;
