import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function TrustPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-5xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <p className="inline-flex rounded-full border border-violet-500/20 bg-violet-500/10 px-3 py-1 text-xs font-medium text-violet-300">Trust information</p>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Review security evidence before relying on a deployment</h1>
        <p className="mt-5 text-base leading-7 text-white/65">
          VoxDesk publishes repository-level information about selected application controls. The public site does not publish an independent audit report, certification, signed compliance attestation, or deployment-specific penetration-test result.
        </p>

        <section aria-labelledby="trust-boundaries-title" className="mt-10 rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-8">
          <h2 id="trust-boundaries-title" className="text-xl font-semibold text-white">What this page can and cannot establish</h2>
          <ul className="mt-5 space-y-3 text-sm leading-6 text-white/60">
            <li>Repository code can show which routes, permissions, response schemas, and data-handling controls are implemented.</li>
            <li>Automated tests exercise specific paths under their test configuration; they do not establish production deployment settings or independent assurance.</li>
            <li>Tenant integration inventory reports stored configuration and recent recorded checks; a stored credential or stale health result is not current provider connectivity.</li>
            <li>Regulatory suitability depends on the data, provider, deployment, contract, operational process, and applicable law.</li>
          </ul>
        </section>

        <aside className="mt-6 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-5 text-sm leading-6 text-amber-50/80">
          This page does not assert SOC 2, HIPAA, GDPR, PCI DSS, ISO 27001, or another certification. Do not submit regulated or sensitive data until the exact deployment and required agreements have been reviewed.
        </aside>

        <div className="mt-8 flex flex-wrap gap-3">
          <a href="/security" className="inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">Security implementation overview</a>
          <a href="/dpa" className="inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">Data processing terms</a>
          <a href="/contact" className="inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Request information</a>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default TrustPage;
