import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsSDKs() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">Official Python & TypeScript SDKs</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Typed client libraries with built-in retries, webhook signature verification, and streaming helpers.</p>
    </GlassCard>
  );
}
export default DocsSDKs;
