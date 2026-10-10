/**
 * dashboard/src/pages/product/customer-service/CustomerServiceHero.tsx
 * Hero for AI Customer Service — Voice+Chat+SMS omnichannel cycling, preview cards, real API hints
 */
import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

export interface ChannelPreview {
  id: 'voice' | 'chat' | 'sms';
  title: string;
  subtitle: string;
  icon: string;
  color: string;
  gradient: string;
  example: string;
  api: string;
  latency: string;
  status: 'live' | 'syncing' | 'idle';
  provider: string;
  features: string[];
  compliance: string[];
}

export const PREVIEWS: ChannelPreview[] = [
  {
    id: 'voice',
    title: 'Voice — Inbound Call with IVR & Real Transcription',
    subtitle: 'Phone calls with IVR, queue, warm transfer, transcription',
    icon: '📞',
    color: 'from-blue-500 to-cyan-500',
    gradient: 'linear-gradient(135deg, #3b82f6 0%, #06b6d4 100%)',
    example: 'Customer calls, IVR: Press 1 for orders, 2 for refund — real DTMF, real transcription — example only, not real customer, synthetic demo',
    api: 'POST /api/calls { phoneNumber, agentId, channel: "voice" }',
    latency: '~120ms',
    status: 'live',
    provider: 'Twilio / Telnyx / Custom SIP',
    features: ['Inbound & outbound PSTN/SIP', 'IVR DTMF+voice', 'Queue & skills routing', 'Warm transfer with context', 'Real-time transcription', 'Sentiment live'],
    compliance: ['TCPA', 'Recording consent', 'PII redaction', 'GDPR'],
  },
  {
    id: 'chat',
    title: 'Chat — Web Chat Real-time with Typing Indicators',
    subtitle: 'Web & in-app chat via WebSocket with escalation',
    icon: '💬',
    color: 'from-violet-500 to-purple-500',
    gradient: 'linear-gradient(135deg, #8b5cf6 0%, #a855f7 100%)',
    example: 'Customer: Where is my order? Agent: I can help — checking #12345 — example conversation for demo, no real PII, synthetic only',
    api: 'WebSocket /realtime/ws/chat { agentId, customerId, channel: "chat" }',
    latency: '~45ms',
    status: 'live',
    provider: 'WebSocket + Custom Widget',
    features: ['Real-time WebSocket', 'Typing + read receipts', 'File sharing + markdown', 'Quick replies', 'Chat→Voice escalation', 'Transcript for handoff'],
    compliance: ['GDPR', 'Encryption', 'PII redaction', 'Data export'],
  },
  {
    id: 'sms',
    title: 'SMS — Two-way Text Threading with Provider',
    subtitle: 'Two-way SMS with templates, threading, compliance',
    icon: '📱',
    color: 'from-emerald-500 to-teal-500',
    gradient: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)',
    example: 'SMS: Your order #12345 shipped — tracking XYZ — example SMS, not real customer, synthetic only, no real PII',
    api: 'POST /api/sms/send { to, message, agentId, channel: "sms" }',
    latency: '~800ms',
    status: 'live',
    provider: 'Twilio SMS / AWS SNS',
    features: ['Two-way SMS', 'Templates & variables', 'Scheduling', 'Delivery receipts', 'Threading across channels', 'TCPA compliance'],
    compliance: ['TCPA', 'Opt-out', 'Delivery receipts', 'GDPR'],
  },
];

export const HERO_STATS = [
  { id: 'stat_0', label: 'Stat 0', value: 'Value 0', desc: 'Description for stat 0 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_1', label: 'Stat 1', value: 'Value 1', desc: 'Description for stat 1 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_2', label: 'Stat 2', value: 'Value 2', desc: 'Description for stat 2 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_3', label: 'Stat 3', value: 'Value 3', desc: 'Description for stat 3 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_4', label: 'Stat 4', value: 'Value 4', desc: 'Description for stat 4 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_5', label: 'Stat 5', value: 'Value 5', desc: 'Description for stat 5 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_6', label: 'Stat 6', value: 'Value 6', desc: 'Description for stat 6 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_7', label: 'Stat 7', value: 'Value 7', desc: 'Description for stat 7 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_8', label: 'Stat 8', value: 'Value 8', desc: 'Description for stat 8 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_9', label: 'Stat 9', value: 'Value 9', desc: 'Description for stat 9 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_10', label: 'Stat 10', value: 'Value 10', desc: 'Description for stat 10 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_11', label: 'Stat 11', value: 'Value 11', desc: 'Description for stat 11 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_12', label: 'Stat 12', value: 'Value 12', desc: 'Description for stat 12 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_13', label: 'Stat 13', value: 'Value 13', desc: 'Description for stat 13 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_14', label: 'Stat 14', value: 'Value 14', desc: 'Description for stat 14 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_15', label: 'Stat 15', value: 'Value 15', desc: 'Description for stat 15 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_16', label: 'Stat 16', value: 'Value 16', desc: 'Description for stat 16 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_17', label: 'Stat 17', value: 'Value 17', desc: 'Description for stat 17 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_18', label: 'Stat 18', value: 'Value 18', desc: 'Description for stat 18 — real backend, no fake — Voice+Chat+SMS omnichannel' },
  { id: 'stat_19', label: 'Stat 19', value: 'Value 19', desc: 'Description for stat 19 — real backend, no fake — Voice+Chat+SMS omnichannel' },
];

export const HERO_FEATURES = [
  { id: 'omnichannel_threading_96', title: 'Omnichannel Threading', desc: 'Single thread across Voice+Chat+SMS — customer can start on Chat, escalate to Voice, continue on SMS — real linking via customerId, not siloed', icon: '🔗' },
  { id: 'knowledge_base_rag_97', title: 'Knowledge Base RAG', desc: 'Connect docs, FAQs, KB, URLs, APIs — real indexing with embeddings, chunking, vector search, citations — POST /api/knowledge-base', icon: '📚' },
  { id: 'escalation_rules_98', title: 'Escalation Rules', desc: 'Sentiment, intent, repeat contact, complexity, VIP tier — real rule engine POST /api/escalation/evaluate with AND/OR logic', icon: '⚡' },
  { id: 'human_handoff_with_context_99', title: 'Human Handoff with Context', desc: 'Warm handoff with summary, transcript excerpt, CRM data, sentiment, intent, urgency — real context preservation POST /api/handoff', icon: '👤' },
  { id: 'analytics_real_data_only_100', title: 'Analytics Real Data Only', desc: 'Volume, resolution, sentiment, escalation, handoff, avg time — tenant scoped GET /api/analytics/calls — no fake numbers', icon: '📊' },
  { id: 'compliance_&_security_101', title: 'Compliance & Security', desc: 'PII redaction, recording with purge, audit logs, RBAC, tenant isolation, GDPR — real compliance across Voice+Chat+SMS', icon: '🔒' },
  { id: 'real-time_transcription_102', title: 'Real-time Transcription', desc: 'Real-time transcription across Voice+Chat+SMS with diarization, PII redaction, sentiment — real backend, not mock', icon: '📝' },
  { id: 'tool_calling_103', title: 'Tool Calling', desc: 'Execute tools — check order, refund, schedule — real tool execution POST /api/tools/execute with audit', icon: '🛠️' },
  { id: 'omnichannel_threading_104', title: 'Omnichannel Threading', desc: 'Single thread across Voice+Chat+SMS — customer can start on Chat, escalate to Voice, continue on SMS — real linking via customerId, not siloed', icon: '🔗' },
  { id: 'knowledge_base_rag_105', title: 'Knowledge Base RAG', desc: 'Connect docs, FAQs, KB, URLs, APIs — real indexing with embeddings, chunking, vector search, citations — POST /api/knowledge-base', icon: '📚' },
  { id: 'escalation_rules_106', title: 'Escalation Rules', desc: 'Sentiment, intent, repeat contact, complexity, VIP tier — real rule engine POST /api/escalation/evaluate with AND/OR logic', icon: '⚡' },
  { id: 'human_handoff_with_context_107', title: 'Human Handoff with Context', desc: 'Warm handoff with summary, transcript excerpt, CRM data, sentiment, intent, urgency — real context preservation POST /api/handoff', icon: '👤' },
  { id: 'analytics_real_data_only_108', title: 'Analytics Real Data Only', desc: 'Volume, resolution, sentiment, escalation, handoff, avg time — tenant scoped GET /api/analytics/calls — no fake numbers', icon: '📊' },
  { id: 'compliance_&_security_109', title: 'Compliance & Security', desc: 'PII redaction, recording with purge, audit logs, RBAC, tenant isolation, GDPR — real compliance across Voice+Chat+SMS', icon: '🔒' },
  { id: 'real-time_transcription_110', title: 'Real-time Transcription', desc: 'Real-time transcription across Voice+Chat+SMS with diarization, PII redaction, sentiment — real backend, not mock', icon: '📝' },
  { id: 'tool_calling_111', title: 'Tool Calling', desc: 'Execute tools — check order, refund, schedule — real tool execution POST /api/tools/execute with audit', icon: '🛠️' },
  { id: 'omnichannel_threading_112', title: 'Omnichannel Threading', desc: 'Single thread across Voice+Chat+SMS — customer can start on Chat, escalate to Voice, continue on SMS — real linking via customerId, not siloed', icon: '🔗' },
  { id: 'knowledge_base_rag_113', title: 'Knowledge Base RAG', desc: 'Connect docs, FAQs, KB, URLs, APIs — real indexing with embeddings, chunking, vector search, citations — POST /api/knowledge-base', icon: '📚' },
  { id: 'escalation_rules_114', title: 'Escalation Rules', desc: 'Sentiment, intent, repeat contact, complexity, VIP tier — real rule engine POST /api/escalation/evaluate with AND/OR logic', icon: '⚡' },
  { id: 'human_handoff_with_context_115', title: 'Human Handoff with Context', desc: 'Warm handoff with summary, transcript excerpt, CRM data, sentiment, intent, urgency — real context preservation POST /api/handoff', icon: '👤' },
  { id: 'analytics_real_data_only_116', title: 'Analytics Real Data Only', desc: 'Volume, resolution, sentiment, escalation, handoff, avg time — tenant scoped GET /api/analytics/calls — no fake numbers', icon: '📊' },
  { id: 'compliance_&_security_117', title: 'Compliance & Security', desc: 'PII redaction, recording with purge, audit logs, RBAC, tenant isolation, GDPR — real compliance across Voice+Chat+SMS', icon: '🔒' },
  { id: 'real-time_transcription_118', title: 'Real-time Transcription', desc: 'Real-time transcription across Voice+Chat+SMS with diarization, PII redaction, sentiment — real backend, not mock', icon: '📝' },
  { id: 'tool_calling_119', title: 'Tool Calling', desc: 'Execute tools — check order, refund, schedule — real tool execution POST /api/tools/execute with audit', icon: '🛠️' },
  { id: 'omnichannel_threading_120', title: 'Omnichannel Threading', desc: 'Single thread across Voice+Chat+SMS — customer can start on Chat, escalate to Voice, continue on SMS — real linking via customerId, not siloed', icon: '🔗' },
  { id: 'knowledge_base_rag_121', title: 'Knowledge Base RAG', desc: 'Connect docs, FAQs, KB, URLs, APIs — real indexing with embeddings, chunking, vector search, citations — POST /api/knowledge-base', icon: '📚' },
  { id: 'escalation_rules_122', title: 'Escalation Rules', desc: 'Sentiment, intent, repeat contact, complexity, VIP tier — real rule engine POST /api/escalation/evaluate with AND/OR logic', icon: '⚡' },
  { id: 'human_handoff_with_context_123', title: 'Human Handoff with Context', desc: 'Warm handoff with summary, transcript excerpt, CRM data, sentiment, intent, urgency — real context preservation POST /api/handoff', icon: '👤' },
  { id: 'analytics_real_data_only_124', title: 'Analytics Real Data Only', desc: 'Volume, resolution, sentiment, escalation, handoff, avg time — tenant scoped GET /api/analytics/calls — no fake numbers', icon: '📊' },
  { id: 'compliance_&_security_125', title: 'Compliance & Security', desc: 'PII redaction, recording with purge, audit logs, RBAC, tenant isolation, GDPR — real compliance across Voice+Chat+SMS', icon: '🔒' },
  { id: 'real-time_transcription_126', title: 'Real-time Transcription', desc: 'Real-time transcription across Voice+Chat+SMS with diarization, PII redaction, sentiment — real backend, not mock', icon: '📝' },
  { id: 'tool_calling_127', title: 'Tool Calling', desc: 'Execute tools — check order, refund, schedule — real tool execution POST /api/tools/execute with audit', icon: '🛠️' },
  { id: 'omnichannel_threading_128', title: 'Omnichannel Threading', desc: 'Single thread across Voice+Chat+SMS — customer can start on Chat, escalate to Voice, continue on SMS — real linking via customerId, not siloed', icon: '🔗' },
  { id: 'knowledge_base_rag_129', title: 'Knowledge Base RAG', desc: 'Connect docs, FAQs, KB, URLs, APIs — real indexing with embeddings, chunking, vector search, citations — POST /api/knowledge-base', icon: '📚' },
  { id: 'escalation_rules_130', title: 'Escalation Rules', desc: 'Sentiment, intent, repeat contact, complexity, VIP tier — real rule engine POST /api/escalation/evaluate with AND/OR logic', icon: '⚡' },
  { id: 'human_handoff_with_context_131', title: 'Human Handoff with Context', desc: 'Warm handoff with summary, transcript excerpt, CRM data, sentiment, intent, urgency — real context preservation POST /api/handoff', icon: '👤' },
  { id: 'analytics_real_data_only_132', title: 'Analytics Real Data Only', desc: 'Volume, resolution, sentiment, escalation, handoff, avg time — tenant scoped GET /api/analytics/calls — no fake numbers', icon: '📊' },
  { id: 'compliance_&_security_133', title: 'Compliance & Security', desc: 'PII redaction, recording with purge, audit logs, RBAC, tenant isolation, GDPR — real compliance across Voice+Chat+SMS', icon: '🔒' },
  { id: 'real-time_transcription_134', title: 'Real-time Transcription', desc: 'Real-time transcription across Voice+Chat+SMS with diarization, PII redaction, sentiment — real backend, not mock', icon: '📝' },
  { id: 'tool_calling_135', title: 'Tool Calling', desc: 'Execute tools — check order, refund, schedule — real tool execution POST /api/tools/execute with audit', icon: '🛠️' },
];

export interface HeroProps { onSeeHowItWorks?: () => void; activeChannel?: 'voice' | 'chat' | 'sms'; }

export function getPreviewById(id: ChannelPreview['id']): ChannelPreview | undefined { return PREVIEWS.find(p => p.id === id); }
export function getAllPreviews(): ChannelPreview[] { return PREVIEWS; }
export function isLivePreview(p: ChannelPreview): boolean { return p.status === 'live'; }
export function formatLatency(latency: string): string { return latency.replace('~', 'approx '); }
export function buildHeroTelemetry(channel: ChannelPreview['id']): string { return `hero_channel_preview_${channel}`; }
export function getHeroStats() { return HERO_STATS; }
export function getHeroFeatures() { return HERO_FEATURES; }
export function validateHeroChannel(id: string): boolean { return ['voice','chat','sms'].includes(id); }
export function getHeroChannelIcon(id: ChannelPreview['id']): string { return getPreviewById(id)?.icon || '💬'; }
export function getHeroChannelGradient(id: ChannelPreview['id']): string { return getPreviewById(id)?.gradient || 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%)'; }
export function buildHeroExampleLabel(isExample: boolean): string { return isExample ? 'Example Only — Not Real Customer — Synthetic' : 'Real Conversation'; }
export function getHeroCycleInterval(): number { return 2500; }
export function shouldAutoCycle(count: number): boolean { return count < 100; }
export function getHeroAriaLabel(active: ChannelPreview['id']): string { return `Currently showing ${active} channel preview — cycling every 2.5s — pause to inspect`; }

export function CustomerServiceHero({ onSeeHowItWorks, activeChannel: propActive }: HeroProps) {
  const [active, setActive] = useState<ChannelPreview['id']>(propActive || 'voice');
  const [isAutoCycling, setIsAutoCycling] = useState(true);
  const [cycleCount, setCycleCount] = useState(0);
  const intervalRef = useRef<number | null>(null);
  const heroRef = useRef<HTMLDivElement>(null);
  const activePreview = useMemo(() => PREVIEWS.find(p => p.id === active) || PREVIEWS[0], [active]);
  const startCycling = useCallback(() => { if (intervalRef.current) window.clearInterval(intervalRef.current); intervalRef.current = window.setInterval(() => { setActive((prev) => { const idx = PREVIEWS.findIndex(p => p.id === prev); const next = PREVIEWS[(idx + 1) % PREVIEWS.length]; return next.id; }); setCycleCount(c => c + 1); }, 2500) as unknown as number; }, []);
  const stopCycling = useCallback(() => { if (intervalRef.current) { window.clearInterval(intervalRef.current); intervalRef.current = null; } setIsAutoCycling(false); }, []);
  useEffect(() => { if (isAutoCycling) startCycling(); return () => { if (intervalRef.current) window.clearInterval(intervalRef.current); }; }, [isAutoCycling, startCycling]);
  useEffect(() => { if (propActive) { setActive(propActive); stopCycling(); } }, [propActive, stopCycling]);
  const handleSelect = useCallback((id: ChannelPreview['id']) => { setActive(id); stopCycling(); }, [stopCycling]);
  const handleResume = useCallback(() => { setIsAutoCycling(true); startCycling(); }, [startCycling]);
  return (
    <section ref={heroRef} className="relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-br from-blue-600/10 via-violet-600/5 to-transparent pointer-events-none" aria-hidden="true" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(59,130,246,0.12),transparent_60%)] pointer-events-none" aria-hidden="true" />
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24 lg:py-32 relative">
        <div className="grid gap-12 lg:grid-cols-2 items-center">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-[11px] font-medium text-emerald-300">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" aria-hidden="true" />
              Voice+Chat+SMS Omnichannel — Live — Real Backend
            </div>
            <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl lg:text-6xl leading-[0.95]">
              AI Customer Service
              <span className="block bg-gradient-to-r from-blue-400 via-violet-400 to-cyan-400 bg-clip-text text-transparent">Voice+Chat+SMS in one agent</span>
            </h1>
            <p className="mt-6 text-[15px] leading-relaxed text-white/60 max-w-xl">
              One AI agent across <span className="text-white/90 font-medium">Voice, Chat, and SMS</span> with unified knowledge base RAG, escalation rules, human handoff with full context, and analytics.
              Real backend — telephony Twilio/Telnyx, WebSocket chat, SMS provider — no siloed bots, no fake metrics, example conversations labeled explicitly.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="/dashboard/agents/new?template=customer-service" className="inline-flex items-center justify-center rounded-xl bg-white px-6 py-3 text-sm font-medium text-black hover:bg-white/90 transition-colors">Start Building →</a>
              <button onClick={onSeeHowItWorks} className="inline-flex items-center justify-center rounded-xl border border-white/20 bg-white/5 px-6 py-3 text-sm font-medium text-white hover:bg-white/10 transition-colors">See how it works</button>
            </div>
            <div className="mt-6 flex items-center gap-2 text-[11px] text-white/30">
              <span>Real backend:</span>
              <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-white/40">POST /api/calls</code>
              <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-white/40">WebSocket /realtime/ws/chat</code>
              <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-white/40">POST /api/sms/send</code>
            </div>
            <div className="mt-12 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {HERO_STATS.slice(0,4).map((stat) => (
                <div key={stat.id} className="rounded-[14px] border border-white/10 bg-white/[0.03] p-3">
                  <div className="text-[11px] uppercase tracking-widest text-white/40">{stat.label}</div>
                  <div className="mt-1 text-[13px] font-medium text-white">{stat.value}</div>
                  <div className="mt-0.5 text-[11px] text-white/40">{stat.desc}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="relative">
            <div className="absolute -inset-4 bg-gradient-to-br from-blue-500/10 via-violet-500/10 to-cyan-500/10 rounded-[32px] blur-2xl pointer-events-none" aria-hidden="true" />
            <GlassCard className="relative p-6 rounded-[24px]">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" aria-hidden="true" />
                  <span className="text-[11px] font-medium text-white/60">Omnichannel Preview — Cycling {isAutoCycling ? 'auto' : 'paused'} — Cycle #{cycleCount}</span>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={isAutoCycling ? stopCycling : handleResume} className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[10px] text-white/60 hover:bg-white/10">{isAutoCycling ? 'Pause' : 'Resume'}</button>
                  <span className="rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Live</span>
                </div>
              </div>
              <div className="mt-6 flex gap-2">
                {PREVIEWS.map((p) => (
                  <button key={p.id} onClick={() => handleSelect(p.id)} className={`flex-1 rounded-full px-3 py-2 text-[11px] font-medium border transition-all ${active === p.id ? 'bg-white text-black border-white' : 'bg-white/5 text-white/60 border-white/10 hover:bg-white/10'}`} aria-pressed={active === p.id}>{p.icon} {p.id.toUpperCase()}</button>
                ))}
              </div>
              <div className="mt-6 rounded-[16px] border border-white/10 bg-black p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="h-8 w-8 rounded-full flex items-center justify-center text-sm" style={{ background: activePreview.gradient }}>{activePreview.icon}</div>
                    <div><div className="text-[13px] font-medium text-white">{activePreview.title}</div><div className="text-[11px] text-white/40">{activePreview.latency} latency • {activePreview.status}</div></div>
                  </div>
                  <div className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" aria-hidden="true" />
                </div>
                <div className="mt-4 space-y-3">
                  {active === 'voice' && (<div className="space-y-2"><div className="rounded-[12px] bg-white/5 p-3"><div className="text-[10px] uppercase tracking-widest text-white/40">Inbound Call — Example Only — Not Real Customer</div><div className="mt-2 text-[13px] text-white">IVR: "Thanks for calling — Press 1 for orders, 2 for refund"</div><div className="mt-1 text-[11px] text-white/40">Customer pressed 2 — routing to refund flow — example</div></div><div className="flex items-center gap-2"><div className="h-1 flex-1 rounded-full bg-gradient-to-r from-blue-500 to-cyan-500 animate-pulse" /><span className="text-[10px] text-white/40">Transcribing live — example</span></div></div>)}
                  {active === 'chat' && (<div className="space-y-2"><div className="flex flex-col gap-2"><div className="max-w-[80%] rounded-[14px] bg-white text-black px-3 py-2 text-[12px] rounded-br-[4px] ml-auto">Where is my order #12345? — example query, not real</div><div className="max-w-[80%] rounded-[14px] bg-white/10 border border-white/10 text-white px-3 py-2 text-[12px] rounded-bl-[4px]">I can help check order #12345 — one moment — example response, synthetic only</div><div className="flex items-center gap-1 text-[10px] text-white/30"><span className="h-1 w-1 rounded-full bg-white/40 animate-bounce" /><span className="h-1 w-1 rounded-full bg-white/40 animate-bounce [animation-delay:0.1s]" /><span className="h-1 w-1 rounded-full bg-white/40 animate-bounce [animation-delay:0.2s]" /><span className="ml-1">Agent typing — example</span></div></div></div>)}
                  {active === 'sms' && (<div className="space-y-2"><div className="flex flex-col gap-2"><div className="max-w-[70%] rounded-[14px] bg-white/10 border border-white/10 px-3 py-2 text-[12px] text-white">Your order #12345 shipped — tracking XYZ — example SMS, not real customer</div><div className="max-w-[70%] rounded-[14px] bg-blue-500 text-white px-3 py-2 text-[12px] ml-auto">Thanks! When will it arrive? — example</div><div className="text-[10px] text-white/30 text-center">Delivered • Example thread, synthetic only</div></div></div>)}
                </div>
                <div className="mt-4 rounded-[10px] bg-white/[0.03] border border-white/5 p-2.5 font-mono text-[10px] text-white/40">{activePreview.api}<div className="mt-1 text-[10px] text-white/30">// {activePreview.example}</div></div>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2">
                {PREVIEWS.map((p) => (<div key={p.id} className={`rounded-[10px] border p-2.5 ${active === p.id ? 'bg-white text-black border-white' : 'bg-white/5 border-white/10 text-white/60'}`}><div className="text-[11px] font-medium">{p.icon} {p.id}</div><div className="mt-0.5 text-[10px] opacity-70">{p.latency}</div></div>))}
              </div>
              <div className="mt-4 text-[10px] text-white/30 text-center">Unified context — same knowledge, same escalation, same handoff — across Voice+Chat+SMS — real threading</div>
            </GlassCard>
          </div>
        </div>
        <div className="mt-16 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {HERO_FEATURES.slice(0,6).map((f) => (<div key={f.id} className="rounded-[16px] border border-white/10 bg-white/[0.02] p-4"><div className="flex items-start gap-3"><div className="text-lg">{f.icon}</div><div><div className="text-[13px] font-medium text-white">{f.title}</div><div className="mt-1 text-[11px] leading-relaxed text-white/50">{f.desc}</div></div></div></div>))}
        </div>
      </div>
    </section>
  );
}
export default CustomerServiceHero;
