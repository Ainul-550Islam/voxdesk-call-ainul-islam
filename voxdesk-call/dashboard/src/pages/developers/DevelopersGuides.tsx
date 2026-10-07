import React from 'react';

const GUIDES = [
  {
    title: 'Create and publish an agent',
    description: 'Use the authenticated agent workspace to edit a draft, publish an immutable version, and inspect its saved snapshot.',
    href: '/dashboard/agents',
  },
  {
    title: 'Run a simulation',
    description: 'Use the test surfaces to distinguish a simulated run from provider-backed production calling.',
    href: '/dashboard/simulations',
  },
  {
    title: 'Inspect tenant integration state',
    description: 'Review stored configuration and health-check evidence without exposing credential values.',
    href: '/dashboard/final-parity',
  },
  {
    title: 'Review source-backed API notes',
    description: 'Read representative API paths and the limitations of the public developer information.',
    href: '/docs',
  },
];

export function DevelopersGuides() {
  return (
    <section aria-labelledby="developer-guides-title" className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 id="developer-guides-title" className="text-2xl font-bold text-white">Workspace guides</h2>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-white/60">These links open product surfaces. They are not instructions to provision an external provider or evidence that an operation has succeeded.</p>
      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        {GUIDES.map((guide) => (
          <article key={guide.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
            <h3 className="text-sm font-semibold text-white">{guide.title}</h3>
            <p className="mt-2 text-xs leading-5 text-white/55">{guide.description}</p>
            <a href={guide.href} className="mt-4 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">Open surface →</a>
          </article>
        ))}
      </div>
    </section>
  );
}

export default DevelopersGuides;
