import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsData() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">4. Data — Contact Memory, Transcripts & PII Redaction</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Persist cross-call caller facts, episodic summaries, and PCI/PII-scrubbed transcripts.</p>
    </GlassCard>
  );
}
export default DocsData;
