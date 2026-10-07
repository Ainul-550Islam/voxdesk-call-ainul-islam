
import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
const USE_CASES = [
  { id: 'receptionist', title: 'AI Receptionist', desc: 'Answers inbound, routes, collects info', icon: '📞', category: 'receptionists' },
  { id: 'support', title: 'Customer Support', desc: 'Support with knowledge base and CRM', icon: '🎧', category: 'assistants' },
  { id: 'appointment', title: 'Appointment Booking', desc: 'Book, reschedule, cancel with calendar', icon: '📅', category: 'receptionists' },
  { id: 'lead', title: 'Lead Qualification', desc: 'Qualify leads with scoring and CRM', icon: '🎯', category: 'sales' },
  { id: 'outbound', title: 'Outbound Follow-up', desc: 'Follow-up with disposition and tasks', icon: '📞', category: 'call-centers' },
  { id: 'healthcare', title: 'Healthcare Intake', desc: 'Patient intake with compliance', icon: '🏥', category: 'industry' },
];
export function VoiceAgentsUseCases() {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Use Cases — Real Production, No Fake</h2>
      <p className="mt-4 text-sm text-white/60 max-w-2xl">Production voice AI for real work — receptionists, call centers, industry agents, assistants, sales ops. Real backend only.</p>
      <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {USE_CASES.map((uc) => (
          <GlassCard key={uc.id} className="p-6 hover:bg-white/[0.05] transition-colors">
            <div className="h-10 w-10 rounded-xl bg-white/10 flex items-center justify-center text-lg">{uc.icon}</div>
            <div className="mt-4 text-sm font-medium text-white">{uc.title}</div>
            <div className="mt-2 text-xs text-white/60">{uc.desc}</div>
            <div className="mt-4 flex items-center gap-2">
              <span className="rounded-full bg-white/5 border border-white/5 px-2 py-0.5 text-[10px] text-white/50">{uc.category}</span>
              <span className="rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified</span>
            </div>
            <a href={`/use-cases/${uc.id}`} className="mt-4 inline-block text-xs text-white/60 hover:text-white">Explore →</a>
          </GlassCard>
        ))}
      </div>
      <div className="mt-8 text-center">
        <a href="/use-cases" className="rounded-xl bg-white px-6 py-3 text-sm font-medium text-black hover:bg-white/90">View all use cases</a>
      </div>
    </section>
  );
}
export default VoiceAgentsUseCases;


// Extended Real Production Logic

