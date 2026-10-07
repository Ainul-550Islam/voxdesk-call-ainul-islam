import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function AuthFAQ() {
  return (
    <GlassCard className="p-5">
      <div className="text-xs font-semibold text-white">Supports RBAC & Multi-Environment</div>
      <p className="mt-1.5 text-xs leading-relaxed text-white/60">
        Sign in as Owner, Admin, Manager, Agent, or Viewer with separate Staging and Production telephony environments.
      </p>
    </GlassCard>
  );
}
export default AuthFAQ;
