import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function TermsPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Contract information</p>
        <h1 className="mt-4 text-3xl font-bold text-white sm:text-4xl">Terms of service availability</h1>
        <div className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-6 text-sm leading-6 text-amber-50/80">
          A complete, approved Terms of Service document is not published on this route. This notice is not a substitute agreement, and it does not create or change contractual terms.
        </div>
        <p className="mt-6 text-sm leading-6 text-white/60">
          Obtain the current terms and acceptable-use requirements from VoxDesk before creating an account or placing calls. Separate provider terms, telecommunications rules, consent requirements, and customer policies may also apply.
        </p>
        <a href="/contact" className="mt-5 inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Request current terms</a>
      </main>
      <PublicFooter />
    </div>
  );
}

export default TermsPage;
