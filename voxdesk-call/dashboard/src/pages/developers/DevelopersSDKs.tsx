import React from 'react';

export function DevelopersSDKs() {
  return (
    <section aria-labelledby="developer-client-title" className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
      <div className="rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-6 sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-200">Package verification</p>
        <h2 id="developer-client-title" className="mt-3 text-xl font-semibold text-white">No public SDK package is verified in this repository</h2>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/65">
          This page does not publish package names, version pins, installation commands, generated clients, or retry guarantees for Python, TypeScript, Rust, or another language. The dashboard contains its own internal API client; that is not a supported public SDK.
        </p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/65">
          Use the route inventory and the authentication and permission rules documented for your deployment. Do not treat an internal frontend helper as a stable third-party client contract.
        </p>
        <a href="/docs" className="mt-5 inline-flex min-h-10 items-center rounded-lg border border-white/15 px-4 text-xs font-semibold text-white hover:bg-white/5">
          Read API and authentication notes
        </a>
      </div>
    </section>
  );
}

export default DevelopersSDKs;
