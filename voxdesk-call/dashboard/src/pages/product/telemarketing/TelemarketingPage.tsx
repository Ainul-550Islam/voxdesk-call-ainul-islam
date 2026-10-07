import React, { useEffect } from 'react'
import { PublicFooter } from '../../../components/layout/PublicFooter'
import { PublicHeader } from '../../../components/layout/PublicHeader'

export function buildSeoTitle() {
  return 'Outbound Campaign Operations | VoxDesk'
}

export function buildSeoDescription() {
  return 'Manage tenant-scoped outbound campaigns with explicit dry-run and live-call controls. Live dialing depends on consent, call-window, billing, and telephony configuration.'
}

export function TelemarketingPage() {
  useEffect(() => {
    document.title = buildSeoTitle()
    let description = document.querySelector<HTMLMetaElement>('meta[name="description"]')
    if (!description) {
      description = document.createElement('meta')
      description.name = 'description'
      document.head.appendChild(description)
    }
    description.setAttribute('content', buildSeoDescription())
  }, [])

  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20">
      <PublicHeader />
      <main>
        <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 sm:py-28 lg:px-8">
          <div className="max-w-3xl">
            <p className="inline-flex items-center rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-white/70">
              Outbound campaign operations
            </p>
            <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-6xl">
              Plan campaign work. Keep live dialing under operator control.
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-7 text-white/65 sm:text-lg">
              VoxDesk stores campaigns and lead records within a tenant environment. The authenticated campaign console reads the saved records and backend counters; this public overview does not display sample customers, fabricated call totals, or provider connection badges.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <a href="/campaigns" className="inline-flex min-h-11 items-center justify-center rounded-full bg-white px-5 py-2.5 text-sm font-semibold text-black transition hover:bg-white/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">Open campaign console</a>
              <a href="/product/voice-agents" className="inline-flex min-h-11 items-center justify-center rounded-full border border-white/15 bg-white/5 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">Explore voice agents</a>
            </div>
          </div>
        </section>

        <section aria-labelledby="campaign-data-title" className="border-y border-white/10 bg-white/[0.02]">
          <div className="mx-auto grid max-w-7xl gap-10 px-4 py-16 sm:px-6 lg:grid-cols-3 lg:px-8">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Persisted data</p>
              <h2 id="campaign-data-title" className="mt-3 text-2xl font-semibold text-white">Read the campaign record, not a demo counter.</h2>
              <p className="mt-4 text-sm leading-6 text-white/60">The campaign list is served by the authenticated API. Lead and processed counts come from persisted lead rows in the campaign&apos;s environment; an empty result means there are no matching records in that scope.</p>
            </div>
            <article className="rounded-2xl border border-white/10 bg-black/40 p-6">
              <h3 className="text-base font-semibold text-white">Dry-run mode</h3>
              <p className="mt-3 text-sm leading-6 text-white/60">The campaign API defaults to dry-run mode. It does not contact the telephony provider, but an eligible batch can record attempt state. The console labels this behavior and keeps live dialing behind a separate confirmation.</p>
            </article>
            <article className="rounded-2xl border border-white/10 bg-black/40 p-6">
              <h3 className="text-base font-semibold text-white">Live dialing</h3>
              <p className="mt-3 text-sm leading-6 text-white/60">A live batch requires explicit operator confirmation. The server checks tenant and environment scope, outbound enablement, consent and do-not-call rules, the tenant call window, and billing entitlement when enforcement is enabled. Valid provider credentials and provider availability are also required; no successful call is implied here.</p>
            </article>
          </div>
        </section>

        <section aria-labelledby="deployment-requirements-title" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
          <div className="grid gap-10 lg:grid-cols-[0.8fr_1.2fr]">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">Deployment-specific</p>
              <h2 id="deployment-requirements-title" className="mt-3 text-2xl font-semibold text-white">Configuration is not the same as connection.</h2>
              <p className="mt-4 text-sm leading-6 text-white/60">Live voice, CRM, SMS, and calendar behavior depends on the configured provider and a successful operation. This product overview does not mark any external integration as connected and does not claim that SMS, CRM writeback, calendar booking, or a live LLM/TTS path has been exercised.</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <article className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <h3 className="text-sm font-semibold text-white">Before a live run</h3>
                <p className="mt-2 text-sm leading-6 text-white/60">Confirm the campaign is active, the tenant&apos;s outbound setting is enabled, the selected environment and audience are correct, and the provider is configured for this deployment.</p>
              </article>
              <article className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <h3 className="text-sm font-semibold text-white">After a live run</h3>
                <p className="mt-2 text-sm leading-6 text-white/60">The console distinguishes a provider-accepted call request from an answered or completed call. Review call records and persisted usage for the outcome; do not infer delivery from a button click.</p>
              </article>
            </div>
          </div>
        </section>
      </main>
      <PublicFooter />
    </div>
  )
}

export default TelemarketingPage
