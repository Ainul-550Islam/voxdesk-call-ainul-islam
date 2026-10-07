import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const VERTICALS = [
  { slug: 'healthcare', name: 'Healthcare & Dental Clinics', desc: 'Patient intake, appointment reminders, and HIPAA-ready triage routing.' },
  { slug: 'home-services', name: 'Home Services & Dispatch', desc: 'Emergency HVAC/plumbing dispatch, service window booking, and technician alerts.' },
  { slug: 'real-estate', name: 'Real Estate & Property Management', desc: 'Buyer/renter qualification, showing scheduling, and maintenance intake.' },
  { slug: 'financial-services', name: 'Financial & Insurance Operations', desc: 'Policy inquiries, claims intake, and PCI-scrubbed payment workflows.' },
  { slug: 'legal', name: 'Legal Intake & Case Screening', desc: '24/7 conflict-check intake, practice-area qualification, and attorney scheduling.' },
  { slug: 'logistics', name: 'Logistics & Fleet Coordination', desc: 'Driver check-in calls, delivery ETA confirmations, and exception escalation.' },
];

export function SolutionsIndustries() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 className="text-2xl font-bold text-white sm:text-3xl">Industry Verticals</h2>
      <div className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {VERTICALS.map((v) => (
          <GlassCard key={v.slug} className="p-6">
            <div className="text-base font-semibold text-white">{v.name}</div>
            <p className="mt-2 text-xs leading-relaxed text-white/60">{v.desc}</p>
            <a href={`/industries/${v.slug}`} className="mt-4 inline-flex text-xs font-medium text-violet-300 hover:text-white">
              View Industry Blueprint →
            </a>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default SolutionsIndustries;
