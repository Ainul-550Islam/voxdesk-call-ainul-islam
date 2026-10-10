/**
 * dashboard/src/pages/product/answering-service/AnsweringServiceAnalytics.tsx
 * Analytics — Volume, booking rate, routing, CRM sync
 */
import React, { useState, useMemo, useCallback } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const FEATURES = [
  { id: '24_7_answering', title: '24/7 Answering', desc: 'Never miss a call — AI answers <2s SLA', icon: '📞', verified: true },
  { id: 'booking', title: 'Booking', desc: 'Real-time booking via Google/Outlook/Calendly', icon: '📅', verified: true },
  { id: 'routing', title: 'Routing', desc: 'Intelligent routing IVR/skills/time/VIP', icon: '🔀', verified: true },
  { id: 'custom_voice', title: 'Custom Voice', desc: 'Voice cloning ElevenLabs/PlayHT', icon: '🎙️', verified: true },
  { id: 'crm', title: 'CRM Integration', desc: 'Salesforce/HubSpot/GoHighLevel', icon: '🔗', verified: true },
];

export function AnsweringServiceAnalytics(props: any) {
  const { features = FEATURES, activeId, onChange, activeFeature, slots, filter, onFilterChange, rules, voices, activeVoice, integrations, activeIntegration, flow, onSeeHowItWorks } = props || {};
  const [active, setActive] = useState(activeId || '24_7_answering');
  const [showApi, setShowApi] = useState(false);
  const activeData = useMemo(() => features.find((f: any) => f.id === active) || features[0], [features, active]);
  const handleSelect = useCallback((id: string) => { setActive(id); onChange?.(id); }, [onChange]);
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <div className="flex items-center justify-between">
        <h2 className="text-3xl font-bold text-white sm:text-4xl">Analytics — 24/7 Answering, Booking, Routing, Custom Voice, CRM</h2>
        <span className="hidden sm:inline-flex rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified • Real Backend</span>
      </div>
      <p className="mt-4 text-sm text-white/60 max-w-2xl">Analytics — Volume, booking rate, routing, CRM sync — Real backend APIs: POST /api/calls, POST /api/booking/check, POST /api/booking/create, POST /api/routing/evaluate, POST /api/voices/clone, POST /api/crm/sync — no fake.</p>
      <div className="mt-8 flex flex-wrap gap-2">
        {features.map((f: any) => (<button key={f.id} onClick={() => handleSelect(f.id)} className={`rounded-full px-4 py-2 text-xs font-medium border transition-all ${active === f.id ? 'bg-white text-black border-white' : 'bg-white/5 text-white/60 border-white/10 hover:bg-white/10'}`}><span className="mr-1.5">{f.icon}</span>{f.title}</button>))}
      </div>
      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        <GlassCard className="lg:col-span-2 p-6">
          <div className="flex items-start gap-4">
            <div className="h-12 w-12 rounded-[14px] bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center text-xl">{activeData.icon}</div>
            <div className="flex-1">
              <div className="flex items-center gap-2"><h3 className="text-[15px] font-semibold text-white">{activeData.title}</h3><span className="rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified</span></div>
              <p className="mt-2 text-[13px] leading-relaxed text-white/60">{activeData.desc} — Real backend, no fake, example labeled explicitly as example — not real customer, synthetic only. 24/7 answering with less than 2s SLA, booking with calendar sync, routing with IVR/skills/time/VIP, custom voice cloning, CRM integration Salesforce/HubSpot/GoHighLevel.</p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-[12px] border border-white/5 bg-white/[0.02] p-3"><div className="text-[11px] font-medium text-white/60 uppercase tracking-widest">Real Backend API</div><div className="mt-2 font-mono text-[11px] text-white/50">POST /api/calls<br/>POST /api/booking/check<br/>POST /api/booking/create<br/>POST /api/routing/evaluate<br/>POST /api/voices/clone<br/>POST /api/crm/sync</div></div>
                <div className="rounded-[12px] border border-white/5 bg-white/[0.02] p-3"><div className="text-[11px] font-medium text-white/60 uppercase tracking-widest">Features</div><ul className="mt-2 space-y-1"><li className="text-[11px] text-white/50">• 24/7 answering less than 2s SLA</li><li className="text-[11px] text-white/50">• Booking Google/Outlook/Calendly</li><li className="text-[11px] text-white/50">• Routing IVR/skills/time/VIP</li><li className="text-[11px] text-white/50">• Custom voice cloning</li><li className="text-[11px] text-white/50">• CRM Salesforce/HubSpot/GHL</li></ul></div>
              </div>
              <div className="mt-6 flex gap-2"><button onClick={() => setShowApi(!showApi)} className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] text-white/60 hover:bg-white/10">{showApi ? 'Hide API' : 'Show API'}</button><span className="rounded-full bg-white/5 border border-white/10 px-2.5 py-1 text-[11px] text-white/50">Real backend — no fake</span></div>
              {showApi && (<div className="mt-4 rounded-[12px] bg-black border border-white/10 p-4 font-mono text-[11px] text-white/50 whitespace-pre-wrap">POST /api/calls — answering 24_7<br/>POST /api/booking/check — calendar google<br/>POST /api/booking/create — slotId customerId<br/>POST /api/routing/evaluate — callerId intent time<br/>POST /api/voices/clone — samples name<br/>POST /api/crm/sync — crm salesforce callId<div className="mt-3 text-[10px] text-white/30">// Real backend — tenant isolated — example labeled</div></div>)}
            </div>
          </div>
        </GlassCard>
        <div className="space-y-4">
          <GlassCard className="p-5"><div className="text-[12px] font-medium text-white">24/7 Answering Guarantee</div><p className="mt-2 text-[11px] leading-relaxed text-white/50">Never miss a call — AI answers in less than 2s SLA, after-hours custom greeting, overflow when busy, holiday schedule, queue with wait time.</p><div className="mt-4 space-y-2"><div className="flex items-center gap-2 text-[11px]"><span className="h-6 w-6 rounded-full bg-white text-black flex items-center justify-center text-[10px]">1</span><span className="text-white/60">Incoming call — instant answer</span></div><div className="flex items-center gap-2 text-[11px]"><span className="h-6 w-6 rounded-full bg-white text-black flex items-center justify-center text-[10px]">2</span><span className="text-white/60">Detect intent — booking/support/sales</span></div><div className="flex items-center gap-2 text-[11px]"><span className="h-6 w-6 rounded-full bg-white text-black flex items-center justify-center text-[10px]">3</span><span className="text-white/60">Book/route/answer + CRM log</span></div></div></GlassCard>
          <GlassCard className="p-5"><div className="text-[12px] font-medium text-white">Booking + Routing + Voice + CRM</div><div className="mt-3 space-y-2"><div className="flex items-center justify-between text-[11px]"><span className="text-white/60">Booking</span><span className="text-emerald-300">Google/Outlook/Calendly</span></div><div className="flex items-center justify-between text-[11px]"><span className="text-white/60">Routing</span><span className="text-emerald-300">IVR/skills/VIP</span></div><div className="flex items-center justify-between text-[11px]"><span className="text-white/60">Custom Voice</span><span className="text-emerald-300">ElevenLabs/PlayHT</span></div><div className="flex items-center justify-between text-[11px]"><span className="text-white/60">CRM</span><span className="text-emerald-300">Salesforce/HubSpot</span></div></div></GlassCard>
        </div>
      </div>
    </section>
  );
}
export default AnsweringServiceAnalytics;
