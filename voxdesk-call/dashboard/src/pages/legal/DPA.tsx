import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function DPA() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Contract information</p>
        <h1 className="mt-4 text-3xl font-bold text-white sm:text-4xl">Data processing agreement availability</h1>
        <div className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-6 text-sm leading-6 text-amber-50/80">
          This public page is not a Data Processing Addendum, does not include Standard Contractual Clauses, and does not form or amend an agreement. No approved DPA text is published here.
        </div>
        <p className="mt-6 text-sm leading-6 text-white/60">
          Contact VoxDesk to request the current contractual documents and confirm whether they apply to the selected product, data flow, and deployment. Do not rely on a route, feature description, or repository control as a contractual commitment.
        </p>
        <a href="/contact" className="mt-5 inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Request contract information</a>
      </main>
      <PublicFooter />
    </div>
  );
}

export default DPA;
