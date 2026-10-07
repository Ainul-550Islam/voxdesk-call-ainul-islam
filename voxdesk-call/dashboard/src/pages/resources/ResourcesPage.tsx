import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

const RESOURCES = [
  { title: 'Documentation index', description: 'Browse workspace topics and the limits of this product documentation.', href: '/docs' },
  { title: 'Developer route inventory', description: 'Review representative backend routes with their source-backed access boundaries.', href: '/developers' },
  { title: 'Use-case examples', description: 'Read illustrative workflow categories from the public catalog.', href: '/use-cases' },
  { title: 'Integration registry', description: 'View provider identifiers and distinguish code registry entries from tenant connections.', href: '/integrations' },
  { title: 'Outbound operation boundaries', description: 'Understand the difference between dry-run campaign processing and live dialing.', href: '/product/telemarketing' },
];

export function ResourcesPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Product resources</p>
          <h1 className="mt-4 text-4xl font-bold tracking-tight text-white sm:text-5xl">Guides and product references</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            This index links to currently published VoxDesk product surfaces. It does not contain customer case studies, reference deployments, or independently validated implementation blueprints.
          </p>
        </div>
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {RESOURCES.map((resource) => (
            <article key={resource.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <h2 className="text-base font-semibold text-white">{resource.title}</h2>
              <p className="mt-3 text-sm leading-6 text-white/60">{resource.description}</p>
              <a href={resource.href} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">Open resource →</a>
            </article>
          ))}
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default ResourcesPage;
