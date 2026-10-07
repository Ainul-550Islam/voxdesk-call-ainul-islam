import React, { useEffect } from 'react';
import { useUseCaseDetail } from '../../hooks/useUseCaseDetail';
import type { UseCaseCapability, UseCaseDetail, UseCaseIntegration } from '../../types/use-case';

function readinessLabel(detail: UseCaseDetail): string {
  if (detail.supported === true) return 'Verified support';
  if (detail.supported === false) return 'Not configured';
  return 'Catalog example · readiness not established';
}

function CatalogNotice({ detail }: { detail: UseCaseDetail }) {
  const catalogOnly = detail.supported !== true || detail.meta?.content_basis === 'static_catalog_example';
  if (!catalogOnly) return null;
  return (
    <aside
      role="note"
      aria-label="Use case evidence limitation"
      className="rounded-2xl border border-amber-300/20 bg-amber-300/[0.06] p-4 text-sm leading-relaxed text-amber-100/80"
    >
      This page describes a catalog example. It does not verify that your workspace has the required provider credentials, phone number, model, integrations, data controls, or production-ready configuration. Example workflows and dialogue below are illustrative, not completed calls or customer results.
    </aside>
  );
}

function CapabilityCard({ capability }: { capability: UseCaseCapability }) {
  const label = capability.enabled === true
    ? 'Available in catalog'
    : capability.enabled === false
      ? 'Not available'
      : 'Not verified for a workspace';
  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-sm font-semibold text-white">{capability.title}</h3>
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-white/60">{label}</span>
      </div>
      {capability.description && <p className="mb-0 mt-2 text-xs leading-relaxed text-white/60">{capability.description}</p>}
    </li>
  );
}

function IntegrationCard({ integration }: { integration: UseCaseIntegration }) {
  const label = integration.verified === true
    ? 'Verification recorded'
    : integration.verified === false
      ? 'Not verified'
      : 'Catalog listing · connection not checked';
  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-sm font-semibold text-white">{integration.name}</h3>
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-white/60">{label}</span>
      </div>
      {integration.description && <p className="mb-0 mt-2 text-xs leading-relaxed text-white/60">{integration.description}</p>}
      <p className="mb-0 mt-2 text-[11px] text-white/40">Provider connection status is tenant-specific and is not checked on this public catalog page.</p>
    </li>
  );
}

export function UseCasesDetailPage({ slug }: { slug: string }) {
  const { data, error, loading, isNotFound, isError, retry } = useUseCaseDetail(slug);

  useEffect(() => {
    if (data) {
      document.title = `${data.title} · Use Cases · VoxDesk`;
      const description = data.description.slice(0, 300);
      let meta = document.querySelector<HTMLMetaElement>('meta[name="description"]');
      if (!meta) {
        meta = document.createElement('meta');
        meta.name = 'description';
        document.head.appendChild(meta);
      }
      meta.content = description;
    } else {
      document.title = isNotFound ? 'Use Case Not Found · VoxDesk' : 'Use Case · VoxDesk';
    }
  }, [data, isNotFound]);

  if (loading) {
    return (
      <main className="min-h-screen bg-[#090d13] px-4 py-16 text-white sm:px-8" aria-labelledby="use-case-loading-title">
        <div className="mx-auto max-w-4xl">
          <h1 id="use-case-loading-title" className="text-2xl font-semibold">Loading use case</h1>
          <p role="status" className="mt-3 text-sm text-white/60">Reading the public use-case catalog…</p>
        </div>
      </main>
    );
  }

  if (isError) {
    return (
      <main className="min-h-screen bg-[#090d13] px-4 py-16 text-white sm:px-8" aria-labelledby="use-case-error-title">
        <div className="mx-auto max-w-4xl rounded-2xl border border-red-300/20 bg-red-400/[0.05] p-6 sm:p-9">
          <h1 id="use-case-error-title" className="text-2xl font-semibold">Use-case catalog unavailable</h1>
          <p role="alert" className="mt-3 text-sm leading-relaxed text-red-100/80">{error || 'The public catalog request failed.'}</p>
          <button type="button" onClick={retry} className="mt-6 rounded-lg border border-white/20 px-4 py-2 text-sm text-white hover:bg-white/5">Retry</button>
        </div>
      </main>
    );
  }

  if (isNotFound || !data) {
    return (
      <main className="min-h-screen bg-[#090d13] px-4 py-16 text-white sm:px-8" aria-labelledby="use-case-not-found-title">
        <div className="mx-auto max-w-4xl rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-9">
          <h1 id="use-case-not-found-title" className="text-2xl font-semibold">Use case not found</h1>
          <p className="mt-3 text-sm leading-relaxed text-white/60">The requested slug is not present in the public catalog.</p>
          <a className="mt-6 inline-flex rounded-lg bg-white px-4 py-2 text-sm font-medium text-black" href="/use-cases">Browse use cases</a>
        </div>
      </main>
    );
  }

  const readiness = readinessLabel(data);
  const capabilities = data.capabilities || [];
  const workflow = [...(data.workflow || [])].sort((a, b) => a.order - b.order);
  const integrations = data.integrations || [];
  const exampleConversation = data.example_conversation || [];
  const faqs = data.faq || [];

  return (
    <div className="min-h-screen bg-[#090d13] text-white">
      <header className="border-b border-white/10 bg-[#090d13]/95">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-8">
          <a href="/" className="font-semibold tracking-wide text-white">VoxDesk</a>
          <nav aria-label="Public navigation" className="flex flex-wrap gap-4 text-sm text-white/65">
            <a href="/product/voice-agents" className="hover:text-white">AI Voice Agent</a>
            <a href="/use-cases" className="hover:text-white">Use Cases</a>
            <a href="/industries" className="hover:text-white">Industries</a>
            <a href="/integrations" className="hover:text-white">Integrations</a>
            <a href="/pricing" className="hover:text-white">Pricing</a>
          </nav>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-8 px-4 py-10 sm:px-8 sm:py-14">
        <section aria-labelledby="use-case-title" className="grid gap-6 rounded-3xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] p-6 sm:p-10">
          <div className="flex flex-wrap items-center gap-3 text-xs text-white/55">
            <a href="/use-cases" className="underline-offset-4 hover:text-white hover:underline">Use Cases</a>
            <span aria-hidden="true">/</span>
            <span>{data.category_title || data.category}</span>
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1">{readiness}</span>
          </div>
          <div className="max-w-3xl">
            <h1 id="use-case-title" className="text-3xl font-bold tracking-tight sm:text-5xl">{data.title}</h1>
            <p className="mt-4 text-base leading-relaxed text-white/70">{data.long_description || data.description}</p>
            {data.problem && <p className="mt-4 text-sm leading-relaxed text-white/55">Problem context: {data.problem}</p>}
            {data.solution && <p className="mt-2 text-sm leading-relaxed text-white/55">Example approach: {data.solution}</p>}
          </div>
          <CatalogNotice detail={data} />
          <div className="flex flex-wrap gap-3">
            <a href={`/dashboard/agents/new?useCase=${encodeURIComponent(data.slug)}`} className="rounded-xl bg-white px-5 py-3 text-sm font-semibold text-black hover:bg-white/90">Create an agent draft</a>
            <a href="/signup" className="rounded-xl border border-white/20 px-5 py-3 text-sm font-medium text-white hover:bg-white/5">Create a workspace</a>
          </div>
        </section>

        <section aria-labelledby="workflow-title" className="grid gap-4">
          <div>
            <h2 id="workflow-title" className="text-xl font-semibold">Illustrative workflow</h2>
            <p className="mt-1 text-xs text-white/50">These are catalog steps, not a persisted workflow execution.</p>
          </div>
          {workflow.length === 0 ? (
            <p className="rounded-xl border border-dashed border-white/15 p-5 text-sm text-white/55">No workflow details are published for this catalog item.</p>
          ) : (
            <ol className="grid gap-3">
              {workflow.map((step, index) => (
                <li key={`${step.order}:${step.title}:${index}`} className="grid gap-2 rounded-xl border border-white/10 bg-white/[0.03] p-4 sm:grid-cols-[40px_1fr]">
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 text-xs font-semibold">{step.order}</span>
                  <div>
                    <h3 className="m-0 text-sm font-semibold">{step.title}</h3>
                    <p className="mb-0 mt-1 text-xs leading-relaxed text-white/60">{step.description}</p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section aria-labelledby="capabilities-title" className="grid gap-4">
          <div>
            <h2 id="capabilities-title" className="text-xl font-semibold">Capability examples</h2>
            <p className="mt-1 text-xs text-white/50">Catalog associations only; workspace configuration and provider reachability are not tested here.</p>
          </div>
          {capabilities.length === 0 ? <p className="text-sm text-white/55">No capabilities are associated with this item.</p> : (
            <ul className="grid gap-3 sm:grid-cols-2">{capabilities.map((capability: UseCaseCapability) => <CapabilityCard key={capability.id} capability={capability} />)}</ul>
          )}
        </section>

        <section aria-labelledby="integrations-title" className="grid gap-4">
          <div>
            <h2 id="integrations-title" className="text-xl font-semibold">Integration examples</h2>
            <p className="mt-1 text-xs text-white/50">A catalog entry is not evidence that a provider is connected in your tenant.</p>
          </div>
          {integrations.length === 0 ? <p className="text-sm text-white/55">No integration examples are listed.</p> : (
            <ul className="grid gap-3 sm:grid-cols-2">{integrations.map((integration: UseCaseIntegration) => <IntegrationCard key={integration.id} integration={integration} />)}</ul>
          )}
        </section>

        {exampleConversation.length > 0 && (
          <section aria-labelledby="example-title" className="grid gap-3">
            <div>
              <h2 id="example-title" className="text-xl font-semibold">Illustrative conversation</h2>
              <p className="mt-1 text-xs text-amber-100/65">Fictional example text only. This is not a call transcript, customer interaction, or provider-generated answer.</p>
            </div>
            <ol className="grid gap-2 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              {exampleConversation.map((message, index) => (
                <li key={`${message.role}:${index}`} className="rounded-lg bg-black/20 p-3 text-sm">
                  <span className="mr-2 text-xs font-semibold uppercase text-white/45">{message.role === 'user' ? 'Example caller' : message.role === 'agent' ? 'Example agent' : 'Example system'}</span>
                  <span className="text-white/75">{message.content}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {data.security.length > 0 && (
          <section aria-labelledby="security-title" className="grid gap-3">
            <div>
              <h2 id="security-title" className="text-xl font-semibold">Security surface</h2>
              <p className="mt-1 text-xs text-white/50">This catalog does not verify compliance certification, retention behavior, or the settings of any workspace.</p>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2">
              {data.security.map((item, index) => (
                <li key={`${item.id}:${index}`} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
                  <strong className="text-sm">{item.title}</strong>
                  <span className="ml-2 text-xs text-amber-100/65">{item.verified === true ? 'verification recorded' : 'not verified by this public catalog'}</span>
                  {item.description && <p className="mb-0 mt-2 text-xs text-white/55">{item.description}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {faqs.length > 0 && (
          <section aria-labelledby="faq-title" className="grid gap-3">
            <h2 id="faq-title" className="text-xl font-semibold">Catalog FAQ</h2>
            {faqs.map((faq, index) => (
              <details key={`${faq.question}:${index}`} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
                <summary className="cursor-pointer text-sm font-medium">{faq.question}</summary>
                <p className="mb-0 mt-3 text-sm leading-relaxed text-white/60">{faq.answer}</p>
              </details>
            ))}
          </section>
        )}

        <section aria-label="Next steps" className="flex flex-wrap items-center justify-between gap-4 border-t border-white/10 pt-6">
          <p className="m-0 max-w-2xl text-xs leading-relaxed text-white/45">Live calls, model responses, number provisioning, CRM updates, workflows, and billing depend on the configured services and credentials in your workspace.</p>
          <a href="/use-cases" className="text-sm text-white underline underline-offset-4">Return to use cases</a>
        </section>
      </main>
    </div>
  );
}

export default UseCasesDetailPage;
