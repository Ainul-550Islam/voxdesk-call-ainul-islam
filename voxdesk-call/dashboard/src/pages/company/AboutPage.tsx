import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

const PRODUCT_AREAS = [
  {
    title: 'Agent configuration',
    description: 'Create drafts, configure an agent, publish immutable versions, and review saved state in the authenticated workspace.',
    href: '/product/voice-agents',
  },
  {
    title: 'Testing and operations',
    description: 'Use simulations, persisted call records, analytics, and read-only lifecycle inspection to distinguish evidence from assumptions.',
    href: '/dashboard/simulations',
  },
  {
    title: 'Deployment adapters',
    description: 'Telephony, CRM, and calendar provider state is deployment- and tenant-specific; source support does not imply a live connection.',
    href: '/integrations',
  },
];

export function AboutPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">About VoxDesk</p>
          <h1 className="mt-4 text-4xl font-bold tracking-tight text-white sm:text-5xl">A workspace for voice-agent workflows</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            VoxDesk brings agent configuration, versioning, testing, telephony operations, persisted call records, analytics, and tenant-scoped administration into one product workspace. Availability and operating behavior depend on the deployed services and the configuration of each account.
          </p>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {PRODUCT_AREAS.map((area) => (
            <article key={area.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <h2 className="text-base font-semibold text-white">{area.title}</h2>
              <p className="mt-3 text-sm leading-6 text-white/60">{area.description}</p>
              <a href={area.href} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">Explore the product area →</a>
            </article>
          ))}
        </div>
        <p className="mt-8 max-w-4xl text-xs leading-5 text-white/40">
          This page does not publish customer counts, staff biographies, proprietary performance claims, certification status, or uptime commitments.
        </p>
      </main>
      <PublicFooter />
    </div>
  );
}

export default AboutPage;
