import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function ComplianceGDPR() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <GlassCard className="p-6 sm:p-8">
        <h2 className="text-xl font-bold text-white">GDPR & Data Privacy Controls</h2>
        <p className="mt-2 text-xs leading-relaxed text-white/65">
          Full support for Right to Access, Right to Erasure, configurable transcript/recording retention windows, and EU data-processing addenda (DPA).
        </p>
      </GlassCard>
    </section>
  );
}
export default ComplianceGDPR;
