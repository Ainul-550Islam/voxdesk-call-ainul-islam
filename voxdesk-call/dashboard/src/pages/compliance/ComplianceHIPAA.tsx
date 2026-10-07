import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function ComplianceHIPAA() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <GlassCard className="p-6 sm:p-8">
        <h2 className="text-xl font-bold text-white">HIPAA Safeguards & Business Associate Agreement (BAA)</h2>
        <p className="mt-2 text-xs leading-relaxed text-white/65">
          Encrypted PHI handling in transit (TLS 1.3 / SRTP) and at rest (AES-256), role-restricted clinical transcript access, and audit logging of every PHI view.
        </p>
      </GlassCard>
    </section>
  );
}
export default ComplianceHIPAA;
