import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function AuthSecurity() {
  return (
    <GlassCard className="p-5">
      <div className="text-xs font-semibold text-emerald-300">Zero-Storage Token Security</div>
      <p className="mt-1.5 text-xs leading-relaxed text-white/60">
        Access tokens are held strictly in memory (never in localStorage or sessionStorage) and rotated via HttpOnly refresh cookies.
      </p>
    </GlassCard>
  );
}
export default AuthSecurity;
