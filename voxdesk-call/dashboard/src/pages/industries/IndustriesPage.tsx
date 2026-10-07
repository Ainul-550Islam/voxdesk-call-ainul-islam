import React, { useMemo, useState } from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export interface Industry {
  id: string;
  name: string;
  slug: string;
  description: string;
  longDescription: string;
  icon: string;
  useCases: string[];
}

/** Illustrative sector categories; not verified deployments or compliance claims. */
export const INDUSTRIES: Industry[] = [
  {
    id: 'healthcare',
    name: 'Healthcare',
    slug: 'healthcare',
    description: 'Illustrative inquiry-intake and appointment conversation patterns.',
    longDescription: 'Possible voice-agent workflow ideas for healthcare inquiry intake and appointment requests. This sector label does not establish a HIPAA program, BAA, EHR integration, or clinical suitability.',
    icon: '🏥',
    useCases: ['Appointment inquiry', 'General intake', 'Human handoff'],
  },
  {
    id: 'financial-services',
    name: 'Financial services',
    slug: 'financial-services',
    description: 'Illustrative customer inquiry and service-routing patterns.',
    longDescription: 'Possible conversation patterns for customer enquiries and routing. This sector label does not establish financial-services compliance, identity verification, fraud detection, or payment processing.',
    icon: '🏦',
    useCases: ['Account inquiry routing', 'Service request intake', 'Human handoff'],
  },
  {
    id: 'legal',
    name: 'Legal services',
    slug: 'legal',
    description: 'Illustrative consultation-intake and call-routing patterns.',
    longDescription: 'Possible conversation patterns for general intake and consultation requests. This sector label does not establish conflict checking, attorney-client privilege, legal advice, or case-management integration.',
    icon: '⚖️',
    useCases: ['Consultation request', 'Basic intake', 'Office routing'],
  },
  {
    id: 'real-estate',
    name: 'Real estate',
    slug: 'real-estate',
    description: 'Illustrative property inquiry and follow-up patterns.',
    longDescription: 'Possible conversation patterns for property enquiries and lead follow-up. This sector label does not establish listing data, CRM connectivity, calendar writes, or fair-housing compliance.',
    icon: '🏠',
    useCases: ['Property inquiry', 'Showing request', 'Lead follow-up'],
  },
  {
    id: 'dental',
    name: 'Dental',
    slug: 'dental',
    description: 'Illustrative appointment-intake and office-routing patterns.',
    longDescription: 'Possible conversation patterns for appointment requests and office routing. This sector label does not establish clinical triage, insurance verification, a BAA, or dental practice software connectivity.',
    icon: '🦷',
    useCases: ['Appointment request', 'Office information', 'Human handoff'],
  },
  {
    id: 'restaurant',
    name: 'Restaurants',
    slug: 'restaurant',
    description: 'Illustrative reservation inquiry and customer-service patterns.',
    longDescription: 'Possible conversation patterns for reservation enquiries and customer service. This sector label does not establish table availability, order processing, payment collection, or point-of-sale integration.',
    icon: '🍽️',
    useCases: ['Reservation inquiry', 'Hours and location', 'Order routing'],
  },
  {
    id: 'ecommerce',
    name: 'E-commerce',
    slug: 'ecommerce',
    description: 'Illustrative order-support and return-intake patterns.',
    longDescription: 'Possible conversation patterns for order-support requests. This sector label does not establish access to order systems, refunds, payment handling, or commerce-platform integration.',
    icon: '🛒',
    useCases: ['Order question intake', 'Return request routing', 'Product inquiry'],
  },
  {
    id: 'automotive',
    name: 'Automotive',
    slug: 'automotive',
    description: 'Illustrative service-request and appointment-intake patterns.',
    longDescription: 'Possible conversation patterns for service enquiries and appointment requests. This sector label does not establish inventory access, dealership integration, or a confirmed appointment.',
    icon: '🚗',
    useCases: ['Service inquiry', 'Appointment request', 'Lead follow-up'],
  },
];

export function getIndustryBySlug(slug: string): Industry | undefined {
  return INDUSTRIES.find((industry) => industry.slug === slug);
}

export function getIndustriesByUseCase(useCase: string): Industry[] {
  const query = useCase.trim().toLowerCase();
  if (!query) return INDUSTRIES;
  return INDUSTRIES.filter((industry) => industry.useCases.some((item) => item.toLowerCase().includes(query)));
}

export function IndustriesPage() {
  const [search, setSearch] = useState('');
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return INDUSTRIES;
    return INDUSTRIES.filter((industry) =>
      `${industry.name} ${industry.description} ${industry.useCases.join(' ')}`.toLowerCase().includes(query),
    );
  }, [search]);

  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="inline-flex rounded-full border border-violet-500/20 bg-violet-500/10 px-3 py-1 text-xs font-medium text-violet-300">Illustrative sector examples</p>
          <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Explore voice workflow ideas by sector</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            These sector pages are illustrative conversation patterns only. They are not evidence of customer deployments, regulated-data suitability, third-party system access, measured outcomes, or regulatory certification.
          </p>
        </div>

        <aside className="mt-8 rounded-xl border border-amber-300/20 bg-amber-300/[0.05] p-4 text-sm leading-6 text-amber-50/80" role="note">
          Do not send sensitive or regulated information through a workflow unless the exact deployment, provider, data handling, access controls, and required agreements have been reviewed and approved for that use.
        </aside>

        <div className="mt-8 max-w-lg">
          <label htmlFor="industry-search" className="mb-2 block text-xs font-medium text-white/75">Search sector examples</label>
          <input id="industry-search" type="search" value={search} maxLength={120} onChange={(event) => setSearch(event.currentTarget.value)} placeholder="Search sectors or workflow ideas" className="h-11 w-full rounded-lg border border-white/15 bg-white/[0.04] px-3 text-sm text-white placeholder:text-white/35 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-300/30" />
        </div>
        <p className="mt-4 text-xs text-white/50" aria-live="polite">Showing {filtered.length} of {INDUSTRIES.length} illustrative sector examples.</p>

        {filtered.length === 0 ? (
          <p className="mt-6 rounded-xl border border-white/10 p-5 text-sm text-white/60" role="status">No sector examples match this search.</p>
        ) : (
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((industry) => (
              <article key={industry.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
                <div className="text-2xl" aria-hidden="true">{industry.icon}</div>
                <h2 className="mt-4 text-base font-semibold text-white">{industry.name}</h2>
                <p className="mt-2 text-sm leading-6 text-white/60">{industry.description}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {industry.useCases.map((item) => <span key={item} className="rounded-full border border-white/10 px-2.5 py-1 text-[10px] text-white/55">{item}</span>)}
                </div>
                <a href={`/industries/${industry.slug}`} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">View illustrative workflow notes →</a>
              </article>
            ))}
          </div>
        )}
      </main>
      <PublicFooter />
    </div>
  );
}

export default IndustriesPage;
