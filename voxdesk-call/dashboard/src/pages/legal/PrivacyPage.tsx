import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function PrivacyPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Privacy information</p>
        <h1 className="mt-4 text-3xl font-bold text-white sm:text-4xl">Privacy policy availability</h1>
        <div className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-6 text-sm leading-6 text-amber-50/80">
          A complete, approved privacy policy is not published by this application. The previous page included unsupported statements about data roles, universal tenant isolation, PII redaction, and permanent deletion; those statements have been removed rather than treated as legal terms.
        </div>
        <section className="mt-6 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <h2 className="text-base font-semibold text-white">Before sharing personal or sensitive data</h2>
          <p className="mt-3 text-sm leading-6 text-white/60">
            Request current information about data categories, purposes, retention, subprocessors, security controls, deletion procedures, and any required agreements for the exact deployment. Do not assume a product setting or route alone answers those questions.
          </p>
          <a href="/contact" className="mt-5 inline-flex min-h-10 items-center rounded-lg bg-white px-4 text-xs font-semibold text-black hover:bg-white/90">Request privacy information</a>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}

export default PrivacyPage;
