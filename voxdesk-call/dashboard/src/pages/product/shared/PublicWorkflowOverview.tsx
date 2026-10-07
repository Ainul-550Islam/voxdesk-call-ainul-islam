import React, { useEffect } from 'react';
import { PublicFooter } from '../../../components/layout/PublicFooter';
import { PublicHeader } from '../../../components/layout/PublicHeader';

export interface PublicWorkflowExample {
  title: string;
  description: string;
}

export interface PublicWorkflowLink {
  label: string;
  href: string;
}

export interface PublicWorkflowOverviewProps {
  eyebrow: string;
  title: string;
  summary: string;
  workflowExamples: PublicWorkflowExample[];
  operationalNote: string;
  metaTitle: string;
  metaDescription: string;
  links: PublicWorkflowLink[];
}

export function PublicWorkflowOverview({
  eyebrow,
  title,
  summary,
  workflowExamples,
  operationalNote,
  metaTitle,
  metaDescription,
  links,
}: PublicWorkflowOverviewProps) {
  useEffect(() => {
    document.title = metaTitle;
    let description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (!description) {
      description = document.createElement('meta');
      description.name = 'description';
      document.head.appendChild(description);
    }
    description.content = metaDescription;
  }, [metaDescription, metaTitle]);

  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20">
      <PublicHeader />
      <main>
        <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
          <div className="max-w-3xl">
            <p className="inline-flex rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-white/70">
              {eyebrow}
            </p>
            <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-6xl">
              {title}
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-7 text-white/65 sm:text-lg">
              {summary}
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              {links.map((link, index) => (
                <a
                  key={`${link.href}:${link.label}`}
                  href={link.href}
                  className={index === 0
                    ? 'inline-flex min-h-11 items-center justify-center rounded-full bg-white px-5 py-2.5 text-sm font-semibold text-black transition hover:bg-white/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400'
                    : 'inline-flex min-h-11 items-center justify-center rounded-full border border-white/15 bg-white/5 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400'}
                >
                  {link.label}
                </a>
              ))}
            </div>
          </div>
        </section>

        <section aria-labelledby="workflow-examples-title" className="border-y border-white/10 bg-white/[0.02]">
          <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
            <div className="max-w-2xl">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">
                Illustrative patterns
              </p>
              <h2 id="workflow-examples-title" className="mt-3 text-2xl font-semibold text-white sm:text-3xl">
                Workflow examples, not live operational status
              </h2>
              <p className="mt-4 text-sm leading-6 text-white/60">
                These examples describe possible workflows. They do not assert that a third-party provider is connected, that an external action has run, or that a call or customer outcome has occurred.
              </p>
            </div>
            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {workflowExamples.map((example) => (
                <article key={example.title} className="rounded-2xl border border-white/10 bg-black/40 p-6">
                  <h3 className="text-base font-semibold text-white">{example.title}</h3>
                  <p className="mt-3 text-sm leading-6 text-white/60">{example.description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section aria-labelledby="operating-conditions-title" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-[0.8fr_1.2fr]">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">
                Deployment-specific
              </p>
              <h2 id="operating-conditions-title" className="mt-3 text-2xl font-semibold text-white">
                Check configuration and persisted results in the authenticated workspace
              </h2>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <p className="text-sm leading-7 text-white/65">{operationalNote}</p>
              <p className="mt-4 border-t border-white/10 pt-4 text-xs leading-6 text-white/45">
                This public overview has no live data feed. It does not display sample records, fabricated metrics, connection badges, or a successful-operation state.
              </p>
            </div>
          </div>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}

export default PublicWorkflowOverview;
