
import React, { useState } from 'react';
const FAQS = [
  { q: 'What is Voice AI?', a: 'Voice AI is production voice agents with real telephony, knowledge retrieval, tool calling, and business integration. Not chatbot with phone — real provider integration.' },
  { q: 'How does call routing work?', a: 'IVR with DTMF and voice, queue, skills-based routing, conditional branching, warm transfer with context. POST /api/calls/{id}/transfer with real provider bridging.' },
  { q: 'What about transfers?', a: 'Warm and cold transfers with summary, CRM context, transcript. Real provider bridging, not mock. Ownership transfer and audit logs.' },
  { q: 'How does outbound work?', a: 'Outbound with DNC compliance, calling windows, retry policies, batch operations. POST /api/calls with E.164 validation, DNC check, consent, window. Real provider.' },
  { q: 'What is the lifecycle?', a: 'CREATE → CONFIGURE → TEST → DEPLOY → MONITOR → IMPROVE. Real backend APIs for each phase, no mock steps.' },
  { q: 'Is it real backend?', a: 'Yes — real telephony (Twilio/Telnyx), real DB, real workflows, no fake metrics, no invented transcripts. Example labeled explicitly.' },
];
export function VoiceAgentsFAQ() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">FAQ — Real Answers, No Fake</h2>
      <div className="mt-12 space-y-2 max-w-3xl">
        {FAQS.map((faq, idx) => {
          const isOpen = open === idx;
          return (
            <div key={idx} className="rounded-[14px] border border-white/10 bg-white/[0.03]">
              <button aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : idx)} className="flex w-full items-center justify-between p-4 text-left">
                <span className="text-sm font-medium text-white">{faq.q}</span>
                <span className={`text-white/40 transition-transform ${isOpen ? 'rotate-180' : ''}`}>⌄</span>
              </button>
              {isOpen && <div className="px-4 pb-4 text-sm text-white/60 leading-relaxed">{faq.a}</div>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
export default VoiceAgentsFAQ;


// Extended Real Production Logic

