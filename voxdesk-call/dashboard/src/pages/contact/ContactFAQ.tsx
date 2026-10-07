import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function ContactFAQ() {
  return (
    <GlassCard className="p-6">
      <h3 className="text-sm font-semibold text-white">What We Cover in the Technical Demo</h3>
      <p className="mt-2 text-xs leading-relaxed text-white/60">
        Live inbound/outbound SIP call walkthrough, warm transfer state machine inspection, RAG knowledge grounding, and custom CRM webhook integration.
      </p>
    </GlassCard>
  );
}
export default ContactFAQ;
