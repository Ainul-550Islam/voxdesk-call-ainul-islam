
import React, { useState, useMemo } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const CAPS = [
  { id: 'voice', title: 'Voice Channel', desc: 'Phone calls with IVR, queue, transfer', category: 'channel', icon: '📞', verified: true },
  { id: 'chat', title: 'Chat Channel', desc: 'Web chat with real-time and escalation', category: 'channel', icon: '💬', verified: true },
  { id: 'sms', title: 'SMS Channel', desc: 'Two-way SMS with threading', category: 'channel', icon: '📱', verified: true },
  { id: 'knowledge', title: 'Knowledge Base RAG', desc: 'RAG with docs, FAQs, KB, URLs, APIs', category: 'knowledge', icon: '📚', verified: true },
  { id: 'escalation', title: 'Escalation Rules', desc: 'Sentiment, intent, repeat, VIP rules', category: 'escalation', icon: '⚡', verified: true },
  { id: 'handoff', title: 'Human Handoff', desc: 'Warm handoff with full context', category: 'handoff', icon: '👤', verified: true },
  { id: 'analytics', title: 'Analytics', desc: 'Volume, sentiment, escalation, handoff', category: 'analytics', icon: '📊', verified: true },
  { id: 'transcription', title: 'Transcription', desc: 'Real-time transcription across channels', category: 'core', icon: '📝', verified: true },
];

export function CustomerServiceCapabilities() {
  const [activeCat, setActiveCat] = useState('all');
  const filtered = useMemo(() => activeCat === 'all' ? CAPS : CAPS.filter(c => c.category === activeCat), [activeCat]);

  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Capabilities — Voice+Chat+SMS, Knowledge, Escalation, Handoff, Analytics</h2>
      <div className="mt-8 flex flex-wrap gap-2">
        {['all', 'channel', 'knowledge', 'escalation', 'handoff', 'analytics', 'core'].map((cat) => (
          <button key={cat} onClick={() => setActiveCat(cat)} className={`rounded-full px-4 py-2 text-xs font-medium border ${activeCat === cat ? 'bg-white text-black border-white' : 'bg-white/5 text-white/60 border-white/10'}`}>{cat.toUpperCase()}</button>
        ))}
      </div>
      <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {filtered.map((cap) => (
          <GlassCard key={cap.id} className="p-6">
            <div className="text-xl">{cap.icon}</div>
            <div className="mt-3 text-sm font-medium text-white">{cap.title}</div>
            <div className="mt-1 text-xs text-white/60">{cap.desc}</div>
            <div className="mt-3 inline-flex rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified</div>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default CustomerServiceCapabilities;


// Extended Real Production Logic for CustomerServiceCapabilities.tsx

