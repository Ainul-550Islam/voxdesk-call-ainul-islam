import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function TrustControls() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">Technical Security Controls</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">AES-256 encryption at rest, TLS 1.3 in transit, short-lived memory-only JWTs, and 5-tier RBAC.</p>
    </GlassCard>
  );
}
export default TrustControls;
