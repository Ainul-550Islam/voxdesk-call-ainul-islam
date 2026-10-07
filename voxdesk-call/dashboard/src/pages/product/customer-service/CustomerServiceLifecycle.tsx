
import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const LIFECYCLE = [
  { id: 'create', title: 'Create — Customer Service Agent', desc: 'Create agent with Voice+Chat+SMS channels, knowledge base, escalation rules', api: 'POST /api/agents', icon: '🛠️' },
  { id: 'configure', title: 'Configure — Channels & Knowledge', desc: 'Configure Voice, Chat, SMS channels and connect knowledge sources', api: 'PUT /api/agents/{id}/channels', icon: '⚙️' },
  { id: 'escalation', title: 'Escalation — Rules & Triggers', desc: 'Set up escalation rules based on sentiment, intent, repeat, VIP', api: 'POST /api/escalation/rules', icon: '⚡' },
  { id: 'handoff', title: 'Handoff — Human Context', desc: 'Configure human handoff with summary, transcript, CRM, sentiment', api: 'PUT /api/agents/{id}/handoff', icon: '👤' },
  { id: 'test', title: 'Test — Omnichannel Simulation', desc: 'Test Voice+Chat+SMS flows with simulation and example conversations', api: 'POST /api/agents/{id}/test', icon: '🧪' },
  { id: 'deploy', title: 'Deploy — Go Live Omnichannel', desc: 'Deploy with phone numbers, chat widget, SMS provider', api: 'POST /api/agents/{id}/deploy', icon: '🚀' },
  { id: 'monitor', title: 'Monitor — Analytics & QA', desc: 'Monitor conversations, sentiment, escalation, handoff, analytics', api: 'GET /api/analytics/calls', icon: '📊' },
];

export function CustomerServiceLifecycle() {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Lifecycle — Create → Configure → Escalation → Handoff → Test → Deploy → Monitor</h2>
      <p className="mt-4 text-sm text-white/60 max-w-2xl">Full lifecycle for AI Customer Service — from creation to continuous improvement. Real backend APIs for each phase.</p>
      <div className="mt-12 relative">
        <div className="absolute left-4 top-0 bottom-0 w-px bg-gradient-to-b from-white/20 via-white/10 to-transparent hidden lg:block" />
        <div className="space-y-6">
          {LIFECYCLE.map((step, idx) => (
            <div key={step.id} className="relative flex gap-4">
              <div className="hidden lg:flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/15 bg-black text-xs text-white">{idx+1}</div>
              <GlassCard className="flex-1 p-5">
                <div className="flex items-start gap-3">
                  <div className="text-xl">{step.icon}</div>
                  <div className="flex-1">
                    <div className="text-sm font-medium text-white">{step.title}</div>
                    <div className="mt-1 text-xs text-white/60">{step.desc}</div>
                    <div className="mt-3 font-mono text-[11px] text-white/40">{step.api}</div>
                  </div>
                </div>
              </GlassCard>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
export default CustomerServiceLifecycle;


// Extended Real Production Logic for CustomerServiceLifecycle.tsx

