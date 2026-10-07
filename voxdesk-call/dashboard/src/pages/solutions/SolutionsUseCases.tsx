import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const SOLUTION_PILLARS = [
  { id: 'receptionist', title: '24/7 AI Receptionist & Answering', desc: 'Answer every inbound call on the first ring, route by intent, and log caller details.', href: '/product/answering-service' },
  { id: 'support', title: 'Omnichannel Customer Service', desc: 'Resolve Tier-1 support tickets grounded in your RAG knowledge base with warm human escalation.', href: '/product/customer-service' },
  { id: 'appointments', title: 'Automated Appointment Setter', desc: 'Check real-time calendar availability, book slots, send SMS reminders, and handle rescheduling.', href: '/product/appointment-setter' },
  { id: 'outbound', title: 'Compliant Outbound & Lead Qualification', desc: 'Run batch outbound campaigns with centralized DNC scrubbing, calling windows, and CRM write-back.', href: '/product/telemarketing' },
];

export function SolutionsUseCases() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 className="text-2xl font-bold text-white sm:text-3xl">Core Departmental Workflows</h2>
      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        {SOLUTION_PILLARS.map((s) => (
          <GlassCard key={s.id} className="p-6">
            <h3 className="text-lg font-semibold text-white">{s.title}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/65">{s.desc}</p>
            <a href={s.href} className="mt-4 inline-flex text-xs font-semibold text-blue-400 hover:text-blue-300">
              Explore Solution →
            </a>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default SolutionsUseCases;
