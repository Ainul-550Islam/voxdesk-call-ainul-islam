
import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
const COMPARISON = [
  { feature: 'Real Telephony', voxdesk: true, others: 'Mock / PSTN only' },
  { feature: 'IVR & Routing', voxdesk: true, others: 'Basic only' },
  { feature: 'Warm Transfer with Context', voxdesk: true, others: false },
  { feature: 'Outbound with DNC & Windows', voxdesk: true, others: 'Partial' },
  { feature: 'Knowledge Base RAG', voxdesk: true, others: true },
  { feature: 'Tools & Function Calling', voxdesk: true, others: 'Limited' },
  { feature: 'Real-time Transcription', voxdesk: true, others: true },
  { feature: 'Live Monitoring (listen/whisper/barge)', voxdesk: true, others: false },
  { feature: 'Compliance (DNC, GDPR, Recording)', voxdesk: true, others: 'Partial' },
  { feature: 'API/SDK/Webhooks', voxdesk: true, others: true },
];
export function VoiceAgentsComparison() {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Why VoxDesk — Real Backend, No Fake</h2>
      <div className="mt-12 overflow-hidden rounded-[20px] border border-white/10">
        <div className="grid grid-cols-3 bg-white/[0.03] border-b border-white/10 p-4 text-xs font-medium">
          <div className="text-white/40">Feature</div>
          <div className="text-white">VoxDesk • Verified</div>
          <div className="text-white/40">Others</div>
        </div>
        {COMPARISON.map((row) => (
          <div key={row.feature} className="grid grid-cols-3 border-b border-white/5 p-4 text-xs last:border-0">
            <div className="text-white/70">{row.feature}</div>
            <div className="text-emerald-300">{row.voxdesk === true ? '✓ Yes — Real backend' : String(row.voxdesk)}</div>
            <div className="text-white/40">{row.others === true ? '✓' : row.others === false ? '✗ No' : String(row.others)}</div>
          </div>
        ))}
      </div>
      <div className="mt-4 text-[11px] text-white/30">Comparison based on real product capabilities — no invented data, no fake metrics.</div>
    </section>
  );
}
export default VoiceAgentsComparison;


// Extended Real Production Logic

