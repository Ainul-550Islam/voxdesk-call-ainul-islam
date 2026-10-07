import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsReliability() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">6. Reliability — Concurrency Leases, Circuit Breakers & Failover</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Rust/Redis atomic concurrency leases and automatic ASR/LLM/TTS provider failover.</p>
    </GlassCard>
  );
}
export default DocsReliability;
