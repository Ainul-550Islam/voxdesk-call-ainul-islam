import React, { useEffect } from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

interface DocTopic {
  slug: string;
  title: string;
  description: string;
  href: string;
}

const TOPICS: DocTopic[] = [
  {
    slug: 'agents',
    title: 'Agents and immutable versions',
    description: 'Create drafts, publish snapshots, inspect version history, and keep calls bound to the version selected for that call.',
    href: '/dashboard/agents',
  },
  {
    slug: 'testing',
    title: 'Simulation and evaluation',
    description: 'Run a test using the configured test surfaces. A simulation is not a provider-backed phone call.',
    href: '/dashboard/simulations',
  },
  {
    slug: 'telephony',
    title: 'Telephony and call records',
    description: 'Configure phone numbers and inspect saved calls. A route or saved provider binding does not establish carrier connectivity.',
    href: '/dashboard/phone-numbers',
  },
  {
    slug: 'analytics',
    title: 'Analytics',
    description: 'Read tenant-scoped aggregates through protected analytics routes. Empty data is not replaced with a demo value.',
    href: '/dashboard/analytics',
  },
  {
    slug: 'integrations',
    title: 'Integrations and credentials',
    description: 'Inspect tenant-specific provider catalogue and connection evidence in the authenticated workspace. Secret values are not returned by read APIs.',
    href: '/dashboard/final-parity',
  },
  {
    slug: 'api',
    title: 'API route inventory',
    description: 'The developer page lists representative source-backed routes and their access requirements. It is not a complete API contract.',
    href: '/developers',
  },
];

export function DocsPage({ slug }: { slug?: string }) {
  const selectedSlug = slug?.trim().toLowerCase();
  const selected = selectedSlug ? TOPICS.find((topic) => topic.slug === selectedSlug) : undefined;

  useEffect(() => {
    document.title = selected ? `${selected.title} | VoxDesk documentation` : 'Documentation and workspace guides | VoxDesk';
    let description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (!description) {
      description = document.createElement('meta');
      description.name = 'description';
      document.head.appendChild(description);
    }
    description.content = selected
      ? selected.description
      : 'Product and API notes for VoxDesk workspace surfaces. Route availability and provider connectivity are deployment-specific.';
  }, [selected]);

  const visibleTopics = selected ? [selected] : TOPICS;

  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
        <div className="max-w-3xl">
          <p className="inline-flex rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">
            Product documentation index
          </p>
          <h1 className="mt-5 text-4xl font-bold tracking-tight text-white sm:text-5xl">
            VoxDesk workspace and API notes
          </h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            This site documents product surfaces and representative backend routes. It is not a generated OpenAPI specification or a guarantee that an external provider is configured. Confirm route permissions and deployment settings before relying on an operation.
          </p>
        </div>

        <aside className="mt-8 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-5 text-sm leading-6 text-amber-50/80" role="note">
          The backend disables generated OpenAPI and interactive documentation routes in this build. No official Python or TypeScript SDK package is verified here. See the developer portal for the source-backed route inventory and its access notes.
        </aside>

        {selectedSlug && !selected && (
          <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6" role="status">
            <h2 className="text-lg font-semibold text-white">Documentation section not found</h2>
            <p className="mt-2 text-sm text-white/60">The requested section is not in the published product documentation index.</p>
            <a href="/docs" className="mt-4 inline-flex text-sm text-blue-300 underline underline-offset-4">Return to documentation</a>
          </div>
        )}

        <section aria-label="Documentation topics" className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visibleTopics.map((topic) => (
            <article key={topic.slug} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-300">{topic.slug}</p>
              <h2 className="mt-3 text-base font-semibold text-white">{topic.title}</h2>
              <p className="mt-3 text-sm leading-6 text-white/60">{topic.description}</p>
              <a href={topic.href} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">Open related surface →</a>
            </article>
          ))}
        </section>

        <section aria-labelledby="auth-boundary-title" className="mt-12 rounded-2xl border border-white/10 bg-white/[0.025] p-6 sm:p-8">
          <h2 id="auth-boundary-title" className="text-xl font-semibold text-white">Authentication and permission boundary</h2>
          <p className="mt-3 max-w-4xl text-sm leading-6 text-white/60">
            Public catalog and pricing paths are separate from workspace operations. Agent, call, analytics, and integration APIs require authenticated tenant context and operation-specific permissions. Server-side authorization remains authoritative; a public URL or client-side permission check does not grant access.
          </p>
          <a href="/developers" className="mt-5 inline-flex text-sm font-medium text-blue-300 underline underline-offset-4">View representative API routes</a>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}

export default DocsPage;
