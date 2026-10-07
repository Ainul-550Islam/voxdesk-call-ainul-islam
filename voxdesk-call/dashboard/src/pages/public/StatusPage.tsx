import React, { useCallback, useEffect, useState } from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { fetchPublicStatusSummary, PublicSiteApiError } from '../../api/public-site';
import type { PublicSiteStatusSummary } from '../../api/types/public-widget';

function describeError(error: unknown): string {
  if (error instanceof PublicSiteApiError) return error.message;
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'The public status request failed without a readable error message.';
}

export function StatusPage() {
  const [summary, setSummary] = useState<PublicSiteStatusSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await fetchPublicStatusSummary());
    } catch (requestError) {
      setSummary(null);
      setError(describeError(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    document.title = 'Public component checks | VoxDesk';
    let description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (!description) {
      description = document.createElement('meta');
      description.name = 'description';
      document.head.appendChild(description);
    }
    description.content = 'Limited public checks for API-process response, database connectivity, registered widget routes, and signaling configuration. Not an uptime or SLA monitor.';
  }, []);

  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-5xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <p className="inline-flex rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">Limited public checks</p>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">Component status evidence</h1>
        <p className="mt-5 max-w-3xl text-base leading-7 text-white/65">
          This status endpoint reports a small set of checks only. It is not a real-time monitor for every service, an uptime history, an SLA measurement, or an end-to-end provider health check.
        </p>

        {loading && <p className="mt-8 rounded-xl border border-white/10 p-5 text-sm text-white/60" role="status">Loading public component checks…</p>}
        {error && (
          <div className="mt-8 rounded-xl border border-red-300/20 bg-red-400/[0.05] p-5" role="alert">
            <h2 className="text-base font-semibold text-red-100">Status information unavailable</h2>
            <p className="mt-2 text-sm text-red-100/70">{error}</p>
            <button type="button" onClick={() => void load()} className="mt-4 rounded-lg border border-white/20 px-4 py-2 text-xs text-white hover:bg-white/5">Retry</button>
          </div>
        )}
        {!loading && !error && summary && (
          <>
            <section className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold text-white">Overall status: <span className="capitalize">{summary.overall_status.replaceAll('_', ' ')}</span></h2>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">{summary.message}</p>
                </div>
                <time className="text-xs text-white/40" dateTime={summary.checked_at}>Checked {new Date(summary.checked_at).toLocaleString()}</time>
              </div>
            </section>
            <section aria-label="Status components" className="mt-5 grid gap-4 sm:grid-cols-2">
              {summary.components.map((component) => (
                <article key={component.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <h3 className="text-sm font-semibold text-white">{component.name}</h3>
                    <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] capitalize text-white/65">{component.status.replaceAll('_', ' ')}</span>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/60">{component.description}</p>
                  <time className="mt-4 block text-[10px] text-white/35" dateTime={component.updated_at}>Checked {new Date(component.updated_at).toLocaleString()}</time>
                </article>
              ))}
            </section>
            <p className="mt-6 text-xs leading-5 text-white/40">
              A successful database ping does not probe feature-specific queries, queue workers, caches, telephony providers, or all authenticated routes. A registered widget route is not proof of a working chat or voice session.
            </p>
          </>
        )}
      </main>
      <PublicFooter />
    </div>
  );
}

export default StatusPage;
