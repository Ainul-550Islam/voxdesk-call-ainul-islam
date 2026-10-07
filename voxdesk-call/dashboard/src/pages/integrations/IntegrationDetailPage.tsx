import React, { useEffect } from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { INTEGRATION_DIRECTORY } from './integrationCatalog';

export interface IntegrationDetailData {
  id: string;
  name: string;
  family: string;
  registryEvidence: string;
  discoveryPath: string;
  accessBoundary: string;
  description: string;
}

export const INTEGRATION_DETAILS: Record<string, IntegrationDetailData> = Object.fromEntries(
  INTEGRATION_DIRECTORY.map((entry) => [entry.id, entry]),
);

export function getIntegrationDetail(slug: string): IntegrationDetailData | undefined {
  return INTEGRATION_DETAILS[slug];
}

export function getAllIntegrationDetails(): IntegrationDetailData[] {
  return Object.values(INTEGRATION_DETAILS);
}

export function IntegrationDetailPage({ slug }: { slug: string }) {
  const data = getIntegrationDetail(slug);

  useEffect(() => {
    document.title = data ? `${data.name} adapter registry | VoxDesk` : 'Integration entry not found | VoxDesk';
    let description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (!description) {
      description = document.createElement('meta');
      description.name = 'description';
      document.head.appendChild(description);
    }
    description.content = data
      ? `${data.name} backend registry information and tenant-specific verification boundary.`
      : 'The requested provider is not present in the published backend registry inventory.';
  }, [data]);

  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <a href="/integrations" className="text-xs text-blue-300 underline underline-offset-4">Back to integration registry</a>
        {data ? (
          <>
            <p className="mt-8 inline-flex rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs capitalize text-white/65">{data.family} registry entry</p>
            <h1 className="mt-4 text-4xl font-bold tracking-tight text-white">{data.name}</h1>
            <p className="mt-5 text-base leading-7 text-white/65">{data.description}</p>
            <dl className="mt-8 divide-y divide-white/10 rounded-2xl border border-white/10 bg-white/[0.03] px-5">
              <div className="grid gap-2 py-4 sm:grid-cols-[180px_1fr]">
                <dt className="text-xs font-semibold text-white/45">Repository evidence</dt>
                <dd className="m-0 text-sm text-white/70">{data.registryEvidence}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[180px_1fr]">
                <dt className="text-xs font-semibold text-white/45">Provider discovery</dt>
                <dd className="m-0 font-mono text-xs text-white/70">{data.discoveryPath}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[180px_1fr]">
                <dt className="text-xs font-semibold text-white/45">Access boundary</dt>
                <dd className="m-0 text-sm text-white/70">{data.accessBoundary}</dd>
              </div>
            </dl>
            <aside className="mt-6 rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-5 text-sm leading-6 text-amber-50/80">
              A registry entry is implementation evidence only. It does not mean the tenant has configured credentials, a successful health check, an external vendor agreement, or an end-to-end operation. No live probe is performed on this public page.
            </aside>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="/dashboard/final-parity" className="inline-flex min-h-11 items-center rounded-xl bg-white px-5 text-sm font-semibold text-black hover:bg-white/90">Inspect authenticated integration state</a>
              <a href="/developers" className="inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">Read API notes</a>
            </div>
          </>
        ) : (
          <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6" role="status">
            <h1 className="text-2xl font-semibold text-white">Provider entry not found</h1>
            <p className="mt-3 text-sm leading-6 text-white/60">This slug is not present in the backend provider registry shown on the integration index. No fallback provider or connection state is substituted.</p>
          </div>
        )}
      </main>
      <PublicFooter />
    </div>
  );
}

export default IntegrationDetailPage;
