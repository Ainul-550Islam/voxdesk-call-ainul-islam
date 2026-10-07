import React from 'react';

export function ContactHero() {
  return (
    <div className="max-w-2xl">
      <span className="rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">
        Enterprise Sales & Solutions Engineering
      </span>
      <h1 className="mt-4 text-3xl font-bold text-white sm:text-4xl">
        Talk to Our Voice AI Architecture Team
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-white/65">
        Discuss custom SIP trunking, high-concurrency reservations, HIPAA BAA compliance, or multi-agent transfer workflows.
      </p>
    </div>
  );
}
export default ContactHero;
