import React, { useMemo, useState } from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { INTEGRATION_DIRECTORY, INTEGRATION_FAMILIES, type IntegrationFamily } from './integrationCatalog';

export function IntegrationsPage() {
  const [search, setSearch] = useState('');
  const [family, setFamily] = useState<'all' | IntegrationFamily>('all');
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return INTEGRATION_DIRECTORY.filter((entry) => {
      const familyMatches = family === 'all' || entry.family === family;
      const textMatches = !query || `${entry.name} ${entry.family} ${entry.description}`.toLowerCase().includes(query);
      return familyMatches && textMatches;
    });
  }, [family, search]);

  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <div className="max-w-3xl">
          <p className="inline-flex rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">
            Repository provider registry
          </p>
          <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Integration adapter inventory</h1>
          <p className="mt-5 text-base leading-7 text-white/65">
            This directory reflects provider identifiers found in the backend CRM, calendar, and telephony registries. It is not a list of connected accounts, vendor endorsements, subscription entitlements, or verified production operations.
          </p>
        </div>

        <aside className="mt-8 rounded-xl border border-amber-300/20 bg-amber-300/[0.05] p-4 text-sm leading-6 text-amber-50/80">
          Provider configuration and health are tenant-specific. Sign in to inspect integration inventory and recent health-check evidence. A provider name in source code or a saved credential is not the same as a successful operation.
        </aside>

        <section aria-label="Integration directory filters" className="mt-8 grid gap-4 sm:grid-cols-[minmax(220px,1fr)_auto] sm:items-end">
          <div>
            <label htmlFor="integration-search" className="mb-2 block text-xs font-medium text-white/75">Search providers</label>
            <input
              id="integration-search"
              type="search"
              maxLength={120}
              value={search}
              onChange={(event) => setSearch(event.currentTarget.value)}
              placeholder="Search provider registry"
              className="h-11 w-full rounded-lg border border-white/15 bg-white/[0.04] px-3 text-sm text-white placeholder:text-white/35 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-300/30"
            />
          </div>
          <div>
            <label htmlFor="integration-family" className="mb-2 block text-xs font-medium text-white/75">Adapter family</label>
            <select
              id="integration-family"
              value={family}
              onChange={(event) => setFamily(event.currentTarget.value as 'all' | IntegrationFamily)}
              className="h-11 w-full rounded-lg border border-white/15 bg-[#121720] px-3 text-sm text-white focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-300/30"
            >
              {INTEGRATION_FAMILIES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </div>
        </section>

        <p className="mt-5 text-xs text-white/50" aria-live="polite">
          Showing {filtered.length} of {INTEGRATION_DIRECTORY.length} repository registry entries. This is not a live tenant inventory.
        </p>

        {filtered.length === 0 ? (
          <div className="mt-6 rounded-xl border border-white/10 bg-white/[0.03] p-6 text-sm text-white/60" role="status">
            No registry entries match this filter.
          </div>
        ) : (
          <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((entry) => (
              <article key={`${entry.family}:${entry.id}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <div className="flex items-start justify-between gap-4">
                  <h2 className="text-base font-semibold text-white">{entry.name}</h2>
                  <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] font-medium capitalize text-white/60">{entry.family}</span>
                </div>
                <p className="mt-3 text-sm leading-6 text-white/60">{entry.description}</p>
                <dl className="mt-4 space-y-2 border-t border-white/10 pt-4 text-[11px]">
                  <div>
                    <dt className="inline text-white/40">Evidence: </dt>
                    <dd className="inline text-white/60">{entry.registryEvidence}</dd>
                  </div>
                  <div>
                    <dt className="inline text-white/40">Discovery: </dt>
                    <dd className="inline font-mono text-white/60">{entry.discoveryPath}</dd>
                  </div>
                  <div>
                    <dt className="inline text-white/40">Boundary: </dt>
                    <dd className="inline text-white/60">{entry.accessBoundary}</dd>
                  </div>
                </dl>
                <a href={`/integrations/${entry.id}`} className="mt-5 inline-flex text-xs font-semibold text-blue-300 hover:text-blue-200">View registry entry →</a>
              </article>
            ))}
          </div>
        )}

        <section className="mt-10 rounded-2xl border border-white/10 bg-white/[0.025] p-6 sm:p-8">
          <h2 className="text-lg font-semibold text-white">Where to check tenant-specific state</h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-white/60">
            Authenticated provider catalogues expose declared capabilities and non-secret configuration fields. The final parity dashboard reports tenant-scoped integration state and stored health-check evidence; it does not initiate calls or perform a live connection probe.
          </p>
          <a href="/dashboard/final-parity" className="mt-5 inline-flex min-h-10 items-center rounded-lg bg-white px-4 text-xs font-semibold text-black hover:bg-white/90">Open integration evidence</a>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}

export default IntegrationsPage;
