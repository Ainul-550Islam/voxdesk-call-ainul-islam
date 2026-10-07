import React from 'react';

export function ComplianceHero() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="max-w-3xl">
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3.5 py-1.5 text-xs font-medium text-emerald-300">
          Regulatory Compliance & Telephony Governance
        </div>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Built for Regulated Industries & Global Privacy Laws
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          Enforce TCPA/DNC calling windows, two-party recording consent disclaimers, GDPR data-subject erasure, HIPAA BAA safeguards, and PCI-DSS transcript redaction.
        </p>
      </div>
    </section>
  );
}
export default ComplianceHero;
