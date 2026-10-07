import React from 'react';

export function SecurityHero() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="max-w-3xl">
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3.5 py-1.5 text-xs font-medium text-emerald-300">
          Enterprise Security Architecture • Zero-Trust Controls
        </div>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Defense-in-Depth Security for Voice AI
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          Strict tenant isolation, short-lived JWT authentication with HttpOnly refresh rotation, server-side KMS secret storage, and immutable audit trails across every API mutation.
        </p>
      </div>
    </section>
  );
}
export default SecurityHero;
