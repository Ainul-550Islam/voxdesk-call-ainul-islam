import React from 'react';

export function HomeTrust() {
  return (
    <section aria-labelledby="customer-proof-heading" className="border-y border-white/10 bg-black px-4 py-10 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-center sm:p-8">
        <h2 id="customer-proof-heading" className="text-xs font-medium uppercase tracking-widest text-white/55">
          Customer proof
        </h2>
        <p className="mx-auto mt-3 max-w-2xl text-sm leading-relaxed text-white/60">
          No customer logos, testimonials, call volumes, or outcome claims are supplied to this page. We do not display fabricated endorsements or performance statistics.
        </p>
      </div>
    </section>
  );
}

export default HomeTrust;
