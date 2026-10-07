import React, { useEffect } from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { PublicWorkflowOverview } from '../product/shared/PublicWorkflowOverview';
import { getIndustryBySlug, INDUSTRIES } from './IndustriesPage';

export interface IndustryDetailData {
  slug: string;
  name: string;
  description: string;
  workflowExamples: Array<{ title: string; description: string }>;
}

const GENERAL_WORKFLOWS = [
  { title: 'Receive an inquiry', description: 'A configured voice or messaging channel may capture a request and provide a next-step prompt.' },
  { title: 'Use tenant-authorized data', description: 'A knowledge source or business system must be configured and authorized before an agent can rely on its data.' },
  { title: 'Route or record the outcome', description: 'An external transfer, booking, message, or record update should be reported only after its provider result is verified and persisted.' },
];

export function getIndustryDetail(slug: string): IndustryDetailData | undefined {
  const industry = getIndustryBySlug(slug);
  if (!industry) return undefined;
  return {
    slug: industry.slug,
    name: industry.name,
    description: industry.longDescription,
    workflowExamples: GENERAL_WORKFLOWS,
  };
}

export function getAllIndustryDetails(): IndustryDetailData[] {
  return INDUSTRIES.map((industry) => ({
    slug: industry.slug,
    name: industry.name,
    description: industry.longDescription,
    workflowExamples: GENERAL_WORKFLOWS,
  }));
}

export function IndustryDetailPage({ slug }: { slug: string }) {
  const detail = getIndustryDetail(slug);

  useEffect(() => {
    document.title = detail ? `${detail.name} workflow ideas | VoxDesk` : 'Industry example not found | VoxDesk';
    let description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (!description) {
      description = document.createElement('meta');
      description.name = 'description';
      document.head.appendChild(description);
    }
    description.content = detail
      ? detail.description
      : 'The requested illustrative sector example is not present in the public index.';
  }, [detail]);

  if (!detail) {
    return (
      <div className="min-h-screen bg-black text-white">
        <PublicHeader />
        <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
          <h1 className="text-3xl font-bold">Industry example not found</h1>
          <p className="mt-4 text-sm leading-6 text-white/60">This sector slug is not in the illustrative public index. No fallback industry or compliance state is substituted.</p>
          <a href="/industries" className="mt-5 inline-flex text-sm text-blue-300 underline underline-offset-4">Browse sector examples</a>
        </main>
        <PublicFooter />
      </div>
    );
  }

  return (
    <PublicWorkflowOverview
      eyebrow={`${detail.name} · illustrative sector example`}
      title={`${detail.name} voice workflow ideas`}
      summary={detail.description}
      metaTitle={`${detail.name} workflow ideas | VoxDesk`}
      metaDescription={detail.description}
      workflowExamples={detail.workflowExamples}
      operationalNote={`This illustrative ${detail.name.toLowerCase()} page does not verify suitability for regulated or sensitive data, integration with sector-specific software, a provider connection, or any customer outcome. Review deployment, provider, access, retention, and legal requirements before use.`}
      links={[
        { label: 'Browse other sector examples', href: '/industries' },
        { label: 'Review workflow examples', href: '/use-cases' },
        { label: 'Contact VoxDesk', href: '/contact' },
      ]}
    />
  );
}

export default IndustryDetailPage;
