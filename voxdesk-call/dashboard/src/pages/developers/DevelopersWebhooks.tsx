import React from 'react';

export function DevelopersWebhooks() {
  return (
    <section aria-labelledby="developer-webhook-title" className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">Provider-specific behavior</p>
        <h2 id="developer-webhook-title" className="mt-3 text-xl font-semibold text-white">Webhook support is not one universal contract</h2>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/65">
          The repository contains provider-specific inbound telephony verification and a CRM webhook integration path. Event formats, signature headers, retry policy, timeout, ordering, and delivery guarantees must be checked against the exact handler and provider configuration; this page does not claim one shared webhook contract for all events.
        </p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/65">
          Secret values are not part of the public route inventory. Tenant administrators can review integration state in the authenticated workspace; provider connectivity is established only by the configured health-check or a real operation, not by a saved URL.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <a href="/dashboard/final-parity" className="inline-flex min-h-10 items-center rounded-lg bg-white px-4 text-xs font-semibold text-black hover:bg-white/90">Open integration evidence</a>
          <a href="/integrations" className="inline-flex min-h-10 items-center rounded-lg border border-white/15 px-4 text-xs font-semibold text-white hover:bg-white/5">Browse provider boundaries</a>
        </div>
      </div>
    </section>
  );
}

export default DevelopersWebhooks;
