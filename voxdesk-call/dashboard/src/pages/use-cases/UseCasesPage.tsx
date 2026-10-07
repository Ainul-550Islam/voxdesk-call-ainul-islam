import React from 'react';
import { useUseCases } from '../../hooks/useUseCases';

function Header() {
  return (
    <header className="sticky top-0 z-20 border-b border-white/10 bg-black/70 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <a href="/" className="flex items-center gap-2" aria-label="VoxDesk home">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-white text-xs font-bold text-black">V</span>
          <span className="text-sm font-semibold text-white">VoxDesk</span>
        </a>
        <nav aria-label="Public navigation" className="hidden items-center gap-5 text-xs text-white/65 md:flex">
          <a href="/product/voice-agents" className="hover:text-white">Voice agents</a>
          <a href="/use-cases" aria-current="page" className="text-white">Use cases</a>
          <a href="/industries" className="hover:text-white">Industries</a>
          <a href="/integrations" className="hover:text-white">Integrations</a>
          <a href="/pricing" className="hover:text-white">Pricing</a>
        </nav>
        <a href="/signup" className="rounded-lg bg-white px-4 py-2 text-xs font-semibold text-black hover:bg-white/90">Create workspace</a>
      </div>
    </header>
  );
}

export function UseCasesPage() {
  const catalog = useUseCases();

  return (
    <div className="min-h-screen bg-[#080b10] text-white">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-12 sm:px-6 sm:py-16 lg:px-8">
        <section aria-labelledby="use-cases-title" className="max-w-3xl">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-sky-300">Public catalog</p>
          <h1 id="use-cases-title" className="mt-3 text-4xl font-bold tracking-tight sm:text-5xl">Voice AI use cases</h1>
          <p className="mt-5 max-w-2xl text-sm leading-7 text-white/65">
            Browse example workflows and capability areas. Availability depends on each workspace's agent configuration, provider credentials, permissions, and environment.
          </p>
        </section>

        <aside className="mt-8 rounded-xl border border-amber-300/20 bg-amber-400/[0.06] p-4 text-sm leading-6 text-amber-50/85" aria-label="Catalog status disclosure">
          Catalog examples only. They are not tenant-specific support confirmations, provider-connectivity checks, compliance certifications, or performance results. Use the workspace APIs to verify your own configuration.
        </aside>

        <section aria-label="Use-case catalog controls" className="mt-8 grid gap-4 md:grid-cols-[minmax(220px,1fr)_minmax(220px,auto)] md:items-end">
          <div>
            <label htmlFor="use-case-search" className="mb-2 block text-xs font-medium text-white/75">Search catalog</label>
            <input
              id="use-case-search"
              type="search"
              maxLength={200}
              value={catalog.search}
              onChange={(event) => catalog.setSearch(event.currentTarget.value)}
              placeholder="Search use cases"
              className="h-11 w-full rounded-lg border border-white/15 bg-white/[0.04] px-3 text-sm text-white placeholder:text-white/35 focus:border-sky-300 focus:outline-none focus:ring-2 focus:ring-sky-300/30"
            />
          </div>
          <div>
            <label htmlFor="use-case-category" className="mb-2 block text-xs font-medium text-white/75">Catalog category</label>
            <select
              id="use-case-category"
              value={catalog.category}
              onChange={(event) => catalog.setCategory(event.currentTarget.value)}
              className="h-11 w-full rounded-lg border border-white/15 bg-[#121720] px-3 text-sm text-white focus:border-sky-300 focus:outline-none focus:ring-2 focus:ring-sky-300/30 md:min-w-64"
            >
              <option value="all">All categories</option>
              {catalog.categories.map((category) => (
                <option key={category.id} value={category.id}>{category.title}</option>
              ))}
            </select>
          </div>
        </section>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-white/55" aria-live="polite">
          <span>{catalog.resultCountLabel}</span>
          <button type="button" onClick={catalog.clearFilters} disabled={!catalog.hasActiveFilters} className="rounded-md border border-white/15 px-3 py-2 text-white/75 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40">Clear filters</button>
        </div>

        {catalog.loading && (
          <div role="status" className="mt-8 rounded-xl border border-white/10 bg-white/[0.03] p-8 text-sm text-white/65">Loading the public use-case catalog…</div>
        )}
        {catalog.error && !catalog.loading && (
          <div role="alert" className="mt-8 rounded-xl border border-red-300/25 bg-red-400/[0.06] p-5">
            <p className="text-sm text-red-100/90">Catalog request failed: {catalog.error}</p>
            <button type="button" onClick={catalog.retry} className="mt-4 rounded-md border border-white/20 px-3 py-2 text-xs text-white hover:bg-white/5">Retry</button>
          </div>
        )}

        {!catalog.loading && !catalog.error && catalog.items.length === 0 && (
          <div className="mt-8 rounded-xl border border-white/10 bg-white/[0.03] p-8 text-center">
            <h2 className="text-lg font-semibold">No catalog entries found</h2>
            <p className="mt-2 text-sm text-white/55">Try another search or select all categories.</p>
            <button type="button" onClick={catalog.clearFilters} className="mt-4 rounded-md bg-white px-3 py-2 text-xs font-semibold text-black">Clear filters</button>
          </div>
        )}

        {!catalog.loading && !catalog.error && catalog.items.length > 0 && (
          <div className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {catalog.items.map((item) => (
              <article key={item.slug} className="flex min-w-0 flex-col rounded-xl border border-white/10 bg-white/[0.035] p-5">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-base font-semibold leading-6">{item.title}</h2>
                  <span className="shrink-0 rounded-full border border-amber-200/20 bg-amber-200/[0.07] px-2.5 py-1 text-[10px] font-medium text-amber-100/80">
                    {item.supported === true ? 'Verified support' : item.supported === false ? 'Not configured' : 'Workspace status unknown'}
                  </span>
                </div>
                <p className="mt-3 flex-1 text-sm leading-6 text-white/60">{item.description}</p>
                <div className="mt-4 flex flex-wrap gap-1.5" aria-label="Illustrative capability areas">
                  {item.capabilities.map((capability) => (
                    <span key={capability} className="rounded-full border border-white/10 px-2.5 py-1 text-[10px] text-white/55">{capability}</span>
                  ))}
                </div>
                <a href={`/use-cases/${item.slug}`} className="mt-5 inline-flex min-h-10 items-center justify-center rounded-lg border border-white/15 px-3 text-xs font-medium text-white hover:bg-white/5 focus:outline-none focus:ring-2 focus:ring-sky-300/50">
                  View catalog example
                </a>
              </article>
            ))}
          </div>
        )}

        {!catalog.loading && !catalog.error && catalog.totalPages > 1 && (
          <nav aria-label="Use-case catalog pages" className="mt-9 flex items-center justify-center gap-4">
            <button type="button" onClick={() => catalog.setPage(catalog.page - 1)} disabled={catalog.page <= 1} className="rounded-lg border border-white/15 px-4 py-2 text-xs text-white disabled:opacity-40">Previous</button>
            <span className="text-xs text-white/60">Page {catalog.page} of {catalog.totalPages}</span>
            <button type="button" onClick={() => catalog.setPage(catalog.page + 1)} disabled={!catalog.hasMore} className="rounded-lg border border-white/15 px-4 py-2 text-xs text-white disabled:opacity-40">Next</button>
          </nav>
        )}
      </main>
      <footer className="border-t border-white/10 px-4 py-7 text-center text-xs text-white/45">© 2026 VoxDesk · Public catalog examples are not a readiness certification.</footer>
    </div>
  );
}

export default UseCasesPage;
