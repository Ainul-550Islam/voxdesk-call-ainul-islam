import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

const AREAS = [
  {
    title: 'Data protection and retention',
    detail: 'Review the privacy policy, data-processing terms, and the selected deployment’s storage and retention settings before sharing personal data.',
    href: '/privacy',
    link: 'Read privacy information',
  },
  {
    title: 'Outbound calling controls',
    detail: 'Campaign operations include tenant-level controls and distinct dry-run and live-dialing paths. Applicable consent, calling windows, and legal obligations still require operator review.',
    href: '/product/telemarketing',
    link: 'Review campaign boundaries',
  },
  {
    title: 'Access and audit surfaces',
    detail: 'Authenticated APIs apply server-side tenant and permission checks to implemented operations. A route or test does not certify an end-to-end compliance program.',
    href: '/security',
    link: 'Read security implementation notes',
  },
];

export function CompliancePage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="inline-flex rounded-full border border-amber-300/20 bg-amber-300/[0.05] px-3 py-1 text-xs font-medium text-amber-100/80">Compliance scope</p>
          <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Application controls do not equal regulatory certification</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            VoxDesk provides software surfaces that may support an organization’s workflows. This public page does not assert that VoxDesk, a specific deployment, or a customer is compliant with HIPAA, GDPR, PCI DSS, TCPA, or another legal framework.
          </p>
        </div>

        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {AREAS.map((area) => (
            <article key={area.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <h2 className="text-base font-semibold text-white">{area.title}</h2>
              <p className="mt-3 text-sm leading-6 text-white/60">{area.detail}</p>
              <a href={area.href} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">{area.link} →</a>
            </article>
          ))}
        </div>

        <aside className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-5 text-sm leading-6 text-amber-50/80">
          Before processing health, payment-card, or other regulated information, confirm the exact data flow, provider contracts, access controls, retention configuration, incident process, and any required business associate or data-processing agreement with qualified counsel and the relevant service providers.
        </aside>
      </main>
      <PublicFooter />
    </div>
  );
}

export default CompliancePage;
