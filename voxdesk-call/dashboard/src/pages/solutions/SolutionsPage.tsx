import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

const SOLUTION_PATTERNS = [
  {
    title: 'Customer-service conversations',
    description: 'Explore voice, chat, knowledge, and handoff surfaces. Channel and provider availability depend on the workspace configuration.',
    href: '/product/customer-service',
  },
  {
    title: 'Appointment intake',
    description: 'Review scheduling workflow concepts. A real availability check or booking requires a configured calendar provider and persisted result.',
    href: '/product/appointment-setter',
  },
  {
    title: 'Inbound answering',
    description: 'Plan number assignment, provider callbacks, agent version binding, and call inspection for an inbound flow.',
    href: '/product/inbound',
  },
  {
    title: 'Outbound campaigns',
    description: 'Review campaign state and explicit dry-run versus live-dialing controls before considering provider-backed calls.',
    href: '/product/telemarketing',
  },
  {
    title: 'Voice-agent development',
    description: 'Create drafts, publish immutable versions, and use test surfaces without confusing simulation with production telephony.',
    href: '/product/voice-agents',
  },
];

export function SolutionsPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="inline-flex rounded-full border border-violet-500/20 bg-violet-500/10 px-3.5 py-1.5 text-xs font-medium text-violet-300">
            Workflow overviews
          </p>
          <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Explore VoxDesk workflow patterns</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            These pages describe product and operator surfaces, not packaged guarantees. Configuration, permissions, provider credentials, and persisted outcomes must be verified for the specific workspace and deployment.
          </p>
        </div>
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {SOLUTION_PATTERNS.map((solution) => (
            <article key={solution.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <h2 className="text-base font-semibold text-white">{solution.title}</h2>
              <p className="mt-3 text-sm leading-6 text-white/60">{solution.description}</p>
              <a href={solution.href} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">Read workflow overview →</a>
            </article>
          ))}
        </div>
        <aside className="mt-8 rounded-xl border border-amber-300/20 bg-amber-300/[0.05] p-4 text-xs leading-5 text-amber-50/75">
          No case studies, customer outcomes, compliance certification, uptime target, or integration-health state is inferred from these illustrative workflow pages.
        </aside>
      </main>
      <PublicFooter />
    </div>
  );
}

export default SolutionsPage;
