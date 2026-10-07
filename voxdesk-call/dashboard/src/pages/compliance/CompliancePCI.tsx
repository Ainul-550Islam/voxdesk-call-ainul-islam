import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function CompliancePCI() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <GlassCard className="p-6 sm:p-8">
        <h2 className="text-xl font-bold text-white">PCI-DSS & TCPA / DNC Enforcement</h2>
        <p className="mt-2 text-xs leading-relaxed text-white/65">
          Automatic PAN/CVV redaction from transcripts and contact memory, plus mandatory DNC registry checks and recipient timezone calling-window enforcement before outbound dialing.
        </p>
      </GlassCard>
    </section>
  );
}
export default CompliancePCI;
