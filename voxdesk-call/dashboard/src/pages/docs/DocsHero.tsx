import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsHero() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">VoxDesk Technical Documentation</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Complete reference organized around Build → Test → Deploy → Data → Monitor → Reliability.</p>
    </GlassCard>
  );
}
export default DocsHero;
