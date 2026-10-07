import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function TrustPrivacy() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">Data Privacy & PII Hygiene</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Automated PII/PCI redaction, configurable retention windows, and strict row-level tenant isolation.</p>
    </GlassCard>
  );
}
export default TrustPrivacy;
