import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function TrustCTA() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">Need a Custom Security Review?</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Connect with our security engineering team for vendor risk assessments and HIPAA BAA execution.</p>
    </GlassCard>
  );
}
export default TrustCTA;
