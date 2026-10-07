
import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const DEV = [
  { id: 'api', title: 'REST API', desc: 'Full API for customer service', code: 'POST /api/agents\nPOST /api/chat/sessions\nPOST /api/sms/send\nPOST /api/handoff' },
  { id: 'chat', title: 'Chat SDK', desc: 'Web chat widget with real-time', code: '<script src="/chat-widget.js">\nVoxDeskChat.init({ agentId })' },
  { id: 'sms', title: 'SMS API', desc: 'Two-way SMS with provider', code: 'POST /api/sms/send\n{ to, message, agentId }' },
  { id: 'knowledge', title: 'Knowledge API', desc: 'RAG with vector search', code: 'POST /api/knowledge-base\n{ type: "docs", url }' },
  { id: 'escalation', title: 'Escalation API', desc: 'Rule engine evaluation', code: 'POST /api/escalation/evaluate\n{ message, sentiment, intent }' },
  { id: 'handoff', title: 'Handoff API', desc: 'Human handoff with context', code: 'POST /api/handoff\n{ summary, transcript, crm }' },
];

export function CustomerServiceDeveloper({ features }: { features?: any }) {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Developer — API for Voice+Chat+SMS, Knowledge, Escalation, Handoff</h2>
      <div className="mt-12 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {DEV.map((d) => (
          <GlassCard key={d.id} className="p-6">
            <div className="text-sm font-medium text-white">{d.title}</div>
            <div className="mt-1 text-xs text-white/60">{d.desc}</div>
            <div className="mt-4 rounded-xl bg-black border border-white/10 p-4 font-mono text-[11px] text-white/50 whitespace-pre-wrap">{d.code}</div>
          </GlassCard>
        ))}
      </div>
      <div className="mt-8 rounded-[16px] border border-white/10 bg-white/[0.03] p-6">
        <div className="text-sm font-medium text-white">Omnichannel Example — Voice+Chat+SMS with Knowledge, Escalation, Handoff, Analytics</div>
        <div className="mt-4 rounded-xl bg-black border border-white/10 p-4 font-mono text-[11px] text-white/60">
          <div>// 1. Customer starts on Chat</div>
          <div>POST /api/chat/sessions {'{'} agentId, customerId {'}'}</div>
          <div className="mt-2">// 2. Knowledge RAG</div>
          <div>POST /api/knowledge/query {'{'} query: "Where is my order?" {'}'}</div>
          <div className="mt-2">// 3. Escalation evaluation</div>
          <div>POST /api/escalation/evaluate {'{'} sentiment: "negative", intent: "refund" {'}'}</div>
          <div className="mt-2">// 4. Human handoff with context</div>
          <div>POST /api/handoff {'{'} summary, transcript, crm, sentiment {'}'}</div>
          <div className="mt-2">// 5. Analytics</div>
          <div>GET /api/analytics/calls?channel=chat&escalated=true</div>
        </div>
      </div>
    </section>
  );
}
export default CustomerServiceDeveloper;


// Extended Real Production Logic for CustomerServiceDeveloper.tsx

