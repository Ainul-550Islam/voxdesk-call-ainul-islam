import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

const CONTROLS = [
  {
    title: 'Authenticated tenant context',
    detail: 'Workspace APIs derive tenant context from authenticated requests; routes use server-side permission dependencies and owned-resource checks.',
    evidence: 'Repository implementation area',
  },
  {
    title: 'Secret-safe integration reads',
    detail: 'The CRM integration response schema uses an explicit field allowlist and does not return stored credential values.',
    evidence: 'CRM integration response model',
  },
  {
    title: 'Provider-specific webhook validation',
    detail: 'Provider webhook handlers include signature and replay protection paths. Validation details vary by handler and do not imply one universal contract.',
    evidence: 'Telephony and CRM webhook modules',
  },
  {
    title: 'Audit surfaces',
    detail: 'The application exposes tenant-scoped audit records for implemented event types; this is not a claim that every system event is captured.',
    evidence: 'Audit API and persistence modules',
  },
];

export function SecurityPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="inline-flex rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">Implementation overview</p>
          <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Security controls and their boundaries</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            The repository contains application controls for authenticated access, tenant-scoped data operations, secret-safe integration responses, webhook validation, and audit records. Code presence and automated tests are not an independent security assessment or proof of a production deployment’s configuration.
          </p>
        </div>

        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {CONTROLS.map((control) => (
            <article key={control.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <h2 className="text-base font-semibold text-white">{control.title}</h2>
              <p className="mt-3 text-sm leading-6 text-white/60">{control.detail}</p>
              <p className="mt-4 border-t border-white/10 pt-3 text-[11px] text-white/40">Evidence area: {control.evidence}</p>
            </article>
          ))}
        </div>

        <aside className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-5 text-sm leading-6 text-amber-50/80">
          No SOC 2, ISO 27001, HIPAA, PCI DSS, GDPR, penetration-test, encryption-key-custody, or uptime certification is claimed on this page. Review applicable terms and deployment evidence with VoxDesk before processing sensitive data.
        </aside>

        <div className="mt-8 flex flex-wrap gap-3">
          <a href="/trust" className="inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">Trust information</a>
          <a href="/privacy" className="inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">Privacy policy</a>
          <a href="/contact" className="inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Ask about security</a>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default SecurityPage;
