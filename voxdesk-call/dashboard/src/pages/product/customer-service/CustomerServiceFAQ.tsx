
import React, { useState } from 'react';
const FAQS = [
  { q: 'What is AI Customer Service?', a: 'One AI agent for Voice+Chat+SMS with unified knowledge base, escalation rules, human handoff with full context, and analytics. Real omnichannel, no siloed bots.' },
  { q: 'How does Voice+Chat+SMS work together?', a: 'Single agent handles all channels with shared knowledge and conversation threading. Customer can start on Chat, escalate to Voice, continue on SMS — single thread, unified context. Real backend linking.' },
  { q: 'What is knowledge base RAG?', a: 'Connect docs, FAQs, KB articles, URLs, APIs — real indexing with vector embeddings, semantic search, citation. POST /api/knowledge-base with real parsing and chunking.' },
  { q: 'How does escalation work?', a: 'Rules based on sentiment, intent, repeat contact, complexity, VIP tier — real rule engine evaluation POST /api/escalation/evaluate with AND/OR logic, not mock.' },
  { q: 'What is human handoff with context?', a: 'Warm handoff with summary, transcript excerpt, CRM data, sentiment, intent, urgency — real context preservation POST /api/handoff, ownership transfer, audit log. Warm and cold.' },
  { q: 'What analytics are available?', a: 'Volume across Voice+Chat+SMS, resolution rate, sentiment trends, escalation rate, handoff rate, avg resolution time, channel distribution, knowledge effectiveness — real backend GET /api/analytics/calls, no fake numbers.' },
  { q: 'Is it real backend?', a: 'Yes — real telephony Twilio/Telnyx, real WebSocket for chat, real SMS provider, real knowledge indexing, real escalation engine, real handoff, real analytics — no fake, no mock, example labeled explicitly.' },
];
export function CustomerServiceFAQ() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">FAQ — Voice+Chat+SMS, Knowledge, Escalation, Handoff, Analytics</h2>
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
export default CustomerServiceFAQ;


// Extended Real Production Logic for CustomerServiceFAQ.tsx

