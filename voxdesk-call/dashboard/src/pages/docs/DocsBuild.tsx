import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsBuild() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">1. Build — Prompts, Dynamic Variables, Knowledge RAG & Tools</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Configure agent prompts with {{variable}} interpolation, attach vector-indexed knowledge bases, and bind JSON-schema tools.</p>
    </GlassCard>
  );
}
export default DocsBuild;
