import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsFAQ() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">Developer & Operations FAQ</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Answers to common questions on webhook signatures, audio codecs (PCMU/Opus), and rate limits.</p>
    </GlassCard>
  );
}
export default DocsFAQ;
